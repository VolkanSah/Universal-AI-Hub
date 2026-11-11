# =============================================================================
# app/pricing.py
# Auto Model Pricing — weil niemand Lust hat, Kosten von Hand zu pflegen.
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# =============================================================================
# ARCHITECTURE NOTE:
#   This file lives exclusively in app/ and is ONLY started by app/app.py.
#   NO direct access to fundaments/*, .env, or Guardian (main.py).
#   All config comes from app/.pyfun [PRICING] via app/config.py.
#
# WHY THIS EXISTS:
#   Not every provider has a pricing API (Anthropic & Google: none).
#   Manually maintaining cost_input_per_1k in [MODELS] sucks. Solution:
#   two free public sources cover almost everything, .pyfun stays the override.
#
# SOURCES (priority: .pyfun manual > OpenRouter > LiteLLM):
#   1. .pyfun [MODELS]           → your values ALWAYS win (manual override)
#   2. OpenRouter /api/v1/models → public, no key. Pricing per token for all
#                                  OR models + context_length + max_completion_tokens
#   3. LiteLLM community JSON    → github.com/BerriAI/litellm main branch.
#                                  input_cost_per_token / output_cost_per_token
#                                  for Anthropic, Gemini, OpenAI, Mistral, ...
#
# PRINCIPLE (same as everything here):
#   No network = no crash. No match = costs stay as configured.
#   Server always starts — pricing refresh runs in the BACKGROUND.
#
# CACHING:
#   Fetched data lands in db_sync hub_state ("pricing.cache") with TTL
#   from .pyfun (default 24h). Restart within TTL = no external call.
#
# MCP TOOLS (registered in mcp.py):
#   get_model_pricing(model_name)  → merged cost info for one model
#   refresh_pricing()              → force refresh, returns source counts
# =============================================================================
import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from . import config
from . import db_sync

logger = logging.getLogger("pricing")

# =============================================================================
# Internal State
# =============================================================================
_cache: Dict[str, Dict[str, Any]] = {}   # normalized_name → {cost_in_1k, cost_out_1k, context, max_out, source}
_meta: Dict[str, Any] = {}               # {"fetched_at": iso, "sources": {...}, "model_count": int}
_task: Optional[asyncio.Task] = None     # background refresh task
_initialized: bool = False

# Loose index: alnum-only keys for dot/dash-insensitive matching.
# Why: OpenRouter writes 'claude-opus-4.6', LiteLLM 'claude-opus-4-6',
# your .pyfun 'claude-opus-4-6'. All should find each other.
_loose_index: Dict[str, str] = {}        # loose_key → normalized cache key
_loose_dirty: bool = True

_HUB_STATE_KEY = "pricing.cache"

# Known vendor prefixes used by OpenRouter / LiteLLM IDs
# ("anthropic/claude-...", "gemini/gemini-...", "meta-llama/llama-...", ...)
_VENDOR_PREFIXES = (
    "anthropic/", "google/", "openai/", "meta-llama/", "mistralai/", "mistral/",
    "qwen/", "nvidia/", "microsoft/", "deepseek/", "x-ai/", "xai/", "perplexity/",
    "cohere/", "gryphe/", "thedrummer/", "undi95/", "cognitivecomputations/",
    "gemini/", "groq/", "together_ai/", "together/", "openrouter/", "fireworks_ai/",
    "azure/", "vertex_ai/", "bedrock/", "cloudflare/", "voyage/", "sambanova/",
    "ai21/", "huggingface/", "ollama/", "ollama_chat/",
)


# =============================================================================
# Normalization — model IDs differ wildly between sources
# =============================================================================
def _normalize(name: str) -> str:
    """
    Normalize a model name for matching across sources.
    'anthropic/claude-haiku-4.5' → 'claude-haiku-4.5'
    'Claude-Haiku-4.5'           → 'claude-haiku-4.5'
    Dots vs dashes are kept — providers are inconsistent there by design.
    """
    n = (name or "").strip().lower()
    for prefix in _VENDOR_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    return n


def _safe_int(value: Any) -> Optional[int]:
    """int() that returns None instead of raising (LiteLLM ships doc samples
    like sample_spec.max_input_tokens = 'max input tokens, if the provider specifies it')."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _per_token_to_per_1k(cost: Any) -> Optional[float]:
    """'0.0000015' per token → 0.0015 per 1k tokens. Returns None on garbage."""
    try:
        val = float(cost)
    except (TypeError, ValueError):
        return None
    if val < 0:  # negative = OpenRouter's "we pay you" marker → treat as 0
        val = 0.0
    return round(val * 1000, 6)


def _loose(name: str) -> str:
    """Strip everything except a-z0-9 — kills dot/dash/underscore differences."""
    return "".join(ch for ch in name if ch.isalnum())


def _rebuild_loose_index() -> None:
    """Rebuild the alnum-only lookup index after a cache change."""
    global _loose_index, _loose_dirty
    _loose_index = {_loose(k): k for k in _cache}
    _loose_dirty = False


def _lookup(model_name: str) -> Optional[Dict[str, Any]]:
    """
    Cache lookup with graceful fallback:
        1. exact normalized match ('claude-opus-4-6')
        2. loose alnum match ('claude-opus-4.6' ≈ 'claude-opus-4-6')
    """
    norm = _normalize(model_name)
    hit = _cache.get(norm)
    if hit:
        return hit
    if _loose_dirty:
        _rebuild_loose_index()
    loose_key = _loose_index.get(_loose(norm))
    if loose_key:
        return _cache.get(loose_key)
    return None


# =============================================================================
# Source Fetchers — each returns {normalized_name: entry}
# =============================================================================
async def _fetch_openrouter(url: str):
    """
    OpenRouter public models endpoint — no key required.
    https://openrouter.ai/api/v1/models → data[].{id, pricing{prompt,completion}, context_length}

    Returns (entries, raw_ids): raw_ids = every real OpenRouter model ID (lowercase) —
    used by check_models() to spot stale/removed models in .pyfun [MODELS].
    """
    out: Dict[str, Dict[str, Any]] = {}
    raw_ids = []
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=30)
        r.raise_for_status()
        payload = r.json()

    for m in payload.get("data", []):
        model_id = m.get("id", "")
        if not model_id:
            continue
        raw_ids.append(model_id.lower())
        pricing = m.get("pricing", {}) or {}
        entry: Dict[str, Any] = {"source": "openrouter"}

        cost_in  = _per_token_to_per_1k(pricing.get("prompt"))
        cost_out = _per_token_to_per_1k(pricing.get("completion"))
        if cost_in is not None:
            entry["cost_input_per_1k"] = cost_in
        if cost_out is not None:
            entry["cost_output_per_1k"] = cost_out
        if m.get("context_length"):
            ctx = _safe_int(m["context_length"])
            if ctx:
                entry["context_tokens"] = ctx

        # max_completion_tokens is nested under top_provider in OR payloads
        max_out = _safe_int((m.get("top_provider") or {}).get("max_completion_tokens"))
        if max_out:
            entry["max_output_tokens"] = max_out

        out[_normalize(model_id)] = entry
    return out, raw_ids


async def _fetch_litellm(url: str) -> Dict[str, Dict[str, Any]]:
    """
    LiteLLM community price list — no key required.
    github.com/BerriAI/litellm → model_prices_and_context_window.json
    Keys like 'claude-sonnet-4-5', 'gemini/gemini-2.5-flash', 'gpt-4o'.
    """
    out: Dict[str, Dict[str, Any]] = {}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=30)
        r.raise_for_status()
        payload = r.json()

    for model_id, info in payload.items():
        if not isinstance(info, dict):
            continue  # skip metadata keys like "additional_vendor_names"
        entry: Dict[str, Any] = {"source": "litellm"}

        cost_in  = _per_token_to_per_1k(info.get("input_cost_per_token"))
        cost_out = _per_token_to_per_1k(info.get("output_cost_per_token"))
        if cost_in is not None:
            entry["cost_input_per_1k"] = cost_in
        if cost_out is not None:
            entry["cost_output_per_1k"] = cost_out
        if info.get("max_input_tokens"):
            ctx = _safe_int(info["max_input_tokens"])
            if ctx:
                entry["context_tokens"] = ctx
        if info.get("max_output_tokens"):
            max_out = _safe_int(info["max_output_tokens"])
            if max_out:
                entry["max_output_tokens"] = max_out

        out[_normalize(model_id)] = entry
    return out


# =============================================================================
# Cache — db_sync hub_state, TTL-gated
# =============================================================================
def _cache_is_fresh() -> bool:
    if not _cache or not _meta.get("fetched_at"):
        return False
    from datetime import datetime, timezone
    try:
        fetched = datetime.fromisoformat(_meta["fetched_at"])
    except (ValueError, TypeError):
        return False
    ttl_hours = float(config.get_pricing().get("PRICING_TTL_HOURS", "24"))
    age = datetime.now(timezone.utc) - fetched
    return age.total_seconds() < ttl_hours * 3600


async def _load_cache_from_db() -> None:
    """Read pricing cache from db_sync — never raises."""
    global _cache, _meta, _loose_dirty
    try:
        stored = await db_sync.read(_HUB_STATE_KEY, default=None)
        if isinstance(stored, dict) and stored.get("models"):
            _cache = stored["models"]
            _meta = stored.get("meta", {})
            _loose_dirty = True
            logger.info(
                f"Pricing cache loaded from db_sync: {len(_cache)} models "
                f"(fetched_at: {_meta.get('fetched_at', 'unknown')})"
            )
    except Exception as e:
        logger.warning(f"Pricing cache load failed (continuing without): {type(e).__name__}")


async def _save_cache_to_db() -> None:
    """Write pricing cache to db_sync — never raises."""
    try:
        await db_sync.write(_HUB_STATE_KEY, {"models": _cache, "meta": _meta})
    except Exception as e:
        logger.warning(f"Pricing cache save failed: {type(e).__name__}")


# =============================================================================
# Refresh — fetch sources, merge, cache
# =============================================================================
async def refresh() -> Dict[str, Any]:
    """
    Fetch all pricing sources, rebuild the auto-pricing cache.
    Never raises — a failing source is just skipped (no fetch = no crash).
    """
    global _cache, _meta, _loose_dirty
    from datetime import datetime, timezone

    cfg       = config.get_pricing()
    or_url    = cfg.get("OPENROUTER_MODELS_URL", "https://openrouter.ai/api/v1/models")
    llm_url   = cfg.get(
        "LITELLM_PRICES_URL",
        "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
    )

    merged: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, int] = {}
    or_ids: list = []

    # --- Source 1: OpenRouter ---
    try:
        or_models, or_ids = await _fetch_openrouter(or_url)
        merged.update(or_models)
        sources["openrouter"] = len(or_models)
        logger.info(f"Pricing source OpenRouter: {len(or_models)} models.")
    except Exception as e:
        sources["openrouter"] = 0
        logger.warning(f"Pricing source OpenRouter failed: {type(e).__name__} — skipped.")

    # --- Source 2: LiteLLM (fills gaps — never overwrites OpenRouter) ---
    try:
        llm_models = await _fetch_litellm(llm_url)
        for name, entry in llm_models.items():
            if name not in merged:
                merged[name] = entry
        sources["litellm"] = len(llm_models)
        logger.info(f"Pricing source LiteLLM: {len(llm_models)} models.")
    except Exception as e:
        sources["litellm"] = 0
        logger.warning(f"Pricing source LiteLLM failed: {type(e).__name__} — skipped.")

    if merged:
        _cache = merged
        _meta = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sources": sources,
            "model_count": len(merged),
            # keep the last known OpenRouter ID list if this fetch failed
            "openrouter_ids": or_ids or _meta.get("openrouter_ids", []),
        }
        _loose_dirty = True
        await _save_cache_to_db()

    stale = check_models()
    if stale:
        logger.warning(f"Models in .pyfun not found in OpenRouter catalog (stale/removed?): {stale}")

    return {
        "fetched_at": _meta.get("fetched_at"),
        "sources": sources,
        "models_cached": len(_cache),
        "stale_models": stale,
    }


# =============================================================================
# Initialization — called by app/app.py, NON-BLOCKING
# =============================================================================
async def initialize() -> None:
    """
    Prepare auto-pricing. Loads the db_sync cache if fresh; otherwise kicks
    off a BACKGROUND refresh (asyncio.create_task) — the server never waits
    for external pricing APIs. No network = no crash.
    """
    global _initialized, _task
    if _initialized:
        return

    cfg = config.get_pricing()
    enabled = cfg.get("PRICING_ENABLED", "true").lower() == "true"

    if not enabled:
        logger.info("Auto-pricing disabled in .pyfun [PRICING] — .pyfun [MODELS] values only.")
        _initialized = True
        return

    await _load_cache_from_db()

    if _cache_is_fresh():
        logger.info("Auto-pricing cache fresh — no external fetch needed.")
    else:
        logger.info("Auto-pricing cache stale/empty — background refresh started.")
        _task = asyncio.create_task(_background_refresh())

    _initialized = True


async def _background_refresh() -> None:
    """Runs refresh() and swallows everything — background task must never crash the app."""
    try:
        await refresh()
    except Exception as e:
        logger.warning(f"Pricing background refresh failed (non-fatal): {type(e).__name__}")


# =============================================================================
# Public API — merged view: .pyfun manual values WIN over auto-fetched
# =============================================================================
async def get_pricing(model_name: str) -> Dict[str, Any]:
    """
    Returns merged pricing info for one model.

    Merge order (highest priority first):
        1. .pyfun [MODELS.<model>]   → your manual values always win
        2. auto-fetched cache        → OpenRouter / LiteLLM
        3. nothing                   → costs omitted, source: "unknown"

    Returns dict with: model, provider (if known from .pyfun),
    cost_input_per_1k, cost_output_per_1k (USD), context_tokens,
    max_output_tokens, source ("pyfun" | "openrouter" | "litellm" | "unknown"),
    pricing_note.
    """
    from . import models as models_mod

    result: Dict[str, Any] = {"model": model_name}
    notes = []

    # --- Layer 1: .pyfun manual config (models.py reads [MODELS]) ---
    manual = models_mod.get(model_name)
    if manual:
        result["provider"] = manual.get("provider", "")
        for key in ("cost_input_per_1k", "cost_output_per_1k",
                    "context_tokens", "max_output_tokens",
                    "requests_per_min", "requests_per_day", "capabilities"):
            if manual.get(key):
                value = manual[key]
                if key.startswith("cost_"):
                    # .pyfun-Werte sind Strings ("0.015") — Auto-Werte sind float.
                    # Normalisieren, damit die API immer Zahlen liefert.
                    try:
                        value = round(float(value), 6)
                    except (TypeError, ValueError):
                        pass
                result[key] = value
        if manual.get("cost_input_per_1k") or manual.get("cost_output_per_1k"):
            result["source"] = "pyfun"
            notes.append(".pyfun manual values (override)")
        else:
            notes.append(".pyfun entry has no costs — auto values used")

    # --- Layer 2: auto-fetched cache fills the gaps ---
    auto = _lookup(model_name)
    if auto:
        for key in ("cost_input_per_1k", "cost_output_per_1k",
                    "context_tokens", "max_output_tokens"):
            if key not in result and auto.get(key) is not None:
                result[key] = auto[key]
        if "source" not in result:
            result["source"] = auto.get("source", "auto")
            notes.append(f"auto-fetched from {auto.get('source', 'unknown')}")
    elif "source" not in result:
        result["source"] = "unknown"
        notes.append("no auto source matched this model name")

    # --- Stale hint ---
    if _cache and not _cache_is_fresh():
        notes.append("cache stale — refresh_pricing() to update")

    result["pricing_note"] = "; ".join(notes) if notes else "ok"
    return result


async def catalog(query: str = "", free_only: bool = False, limit: int = 50) -> Dict[str, Any]:
    """
    Real model IDs of the live OpenRouter catalog (for picking valid [MODELS] entries).
    Fetches the catalog once if it is not loaded yet. Read-only, no key needed.
    """
    if not _meta.get("openrouter_ids"):
        await refresh()
    ids = _meta.get("openrouter_ids") or []
    q = (query or "").strip().lower()
    hits = sorted(i for i in ids if (not free_only or i.endswith(":free")) and (not q or q in i))
    limit = max(1, min(int(limit or 50), 200))
    rows = []
    for i in hits[:limit]:
        info = _lookup(i) or {}
        rows.append({"id": i, "context_tokens": info.get("context_tokens"),
                     "cost_input_per_1k": info.get("cost_input_per_1k"),
                     "cost_output_per_1k": info.get("cost_output_per_1k")})
    return {"in_catalog": len(ids), "matches": len(hits), "shown": len(rows), "models": rows}


def check_models() -> list:
    """
    .pyfun [MODELS] entries with provider "openrouter" whose ID is NOT in the live
    OpenRouter catalog (free models come and go). Empty list if the catalog is unknown.
    """
    ids = set(_meta.get("openrouter_ids") or [])
    if not ids:
        return []
    return sorted(
        name for name, cfg in config.get_models().items()
        if cfg.get("provider") == "openrouter" and name.lower() not in ids
    )


def max_output_for(model_name: str) -> Optional[int]:
    """
    Max output tokens for a model (sync, for clamping): manual .pyfun value wins,
    then auto-fetched. None if unknown → no clamp.
    """
    from . import models as models_mod
    manual = _safe_int((models_mod.get(model_name) or {}).get("max_output_tokens"))
    if manual:
        return manual
    auto = _lookup(model_name) if _cache else None
    return _safe_int(auto.get("max_output_tokens")) if auto else None


async def cost_for(model_name: str, tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
    """
    Estimated USD cost of one call from merged pricing (manual > auto).
    None if no price is known. Anthropic cache read/write surcharges are not modeled.
    """
    info = await get_pricing(model_name)
    c_in, c_out = info.get("cost_input_per_1k"), info.get("cost_output_per_1k")
    if c_in is None and c_out is None:
        return None
    try:
        return round(((tokens_in or 0) / 1000) * float(c_in or 0)
                     + ((tokens_out or 0) / 1000) * float(c_out or 0), 8)
    except (TypeError, ValueError):
        return None


def is_ready() -> bool:
    """True if pricing cache holds at least one model."""
    return bool(_cache)


def stats() -> Dict[str, Any]:
    """Cache stats for list_active_tools / debugging."""
    return {
        "models_cached": len(_cache),
        "fetched_at": _meta.get("fetched_at"),
        "sources": _meta.get("sources", {}),
        "fresh": _cache_is_fresh(),
        "stale_models": check_models(),
    }


# =============================================================================
# Direct execution guard
# =============================================================================
if __name__ == "__main__":
    print("WARNING: Run via main.py → app.py, not directly.")
