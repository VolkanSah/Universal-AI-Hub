# =============================================================================
# app/tools.py
# Tool Registry — Modular Wrapper
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# Repo: https://github.com/VolkanSah/Universal-MCP-Hub-sandboxed
# =============================================================================
# ARCHITECTURE NOTE:
#   This file lives exclusively in app/ and is ONLY started by app/app.py.
#   NO direct access to fundaments/*, .env, or Guardian (main.py).
#   All config comes from app/.pyfun via app/config.py.
#
# TOOL REGISTRY PRINCIPLE:
#   Tools are defined in .pyfun [TOOLS] — never hardcoded here.
#   Adding a new tool = update .pyfun only. Never touch this file.
#   config.py parses [TOOLS] and delivers ready-to-use dicts.
#
# DEPENDENCY CHAIN:
#   .pyfun → config.py → tools.py → mcp.py
#   tools.py delegates execution to providers.py — never calls APIs directly.
# =============================================================================

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from . import config     # reads app/.pyfun — single source of truth
from . import providers  # LLM + Search execution + fallback chain
from . import db_sync

logger = logging.getLogger("tools")

# =============================================================================
# Internal Registry — built from .pyfun [TOOLS] at initialize()
# =============================================================================
_registry: Dict[str, Dict] = {}


# =============================================================================
# Initialization — called by app/app.py (parameterless, sandboxed)
# =============================================================================

def initialize() -> None:
    """
    Builds the tool registry from .pyfun [TOOLS].
    Called once by app/app.py during startup sequence.
    No fundaments passed in — fully sandboxed.

    Loads all active tools and their config (description, provider_type,
    default_provider, timeout_sec, system_prompt, etc.) into _registry.
    Inactive tools (active = "false") are skipped silently.
    """
    global _registry
    _registry = config.get_active_tools()
    logger.info(f"Tools loaded: {list(_registry.keys())}")
    for tname, tcfg in _registry.items():
        skill = tcfg.get("system_prompt_file", "")
        if skill:
            try:
                _load_skill(skill)
                logger.info(f"Tool '{tname}': skill '{skill}' loaded.")
            except ValueError as e:
                logger.warning(f"Tool '{tname}': {e}")


# =============================================================================
# Health — shared by the MCP tool and the REST gateway. Cheap, never raises, never blocks long.
# =============================================================================

async def health() -> Dict[str, Any]:
    """Hub status incl. PostgreSQL awake-check (short ping), queue depth, providers, integrations."""
    from . import pricing, discord_api, gsc
    db = await db_sync.pg_status()
    try:
        queue = (await db_sync.persist_queue_stats()).get("queue_depth")
    except Exception:
        queue = None
    degraded = db.get("bridge") == "active" and db.get("state") != "up"
    return {
        "status":        "degraded" if degraded else "ok",
        "service":       "Universal MCP Hub",
        "database":      db,
        "persist_queue": queue,
        "llm_providers":    providers.list_active_llm(),
        "search_providers": providers.list_active_search(),
        "pricing_ready":    pricing.is_ready(),
        "stale_models":     pricing.check_models(),
        "integrations":     {"discord": discord_api.is_ready(), "gsc": gsc.is_ready()},
    }


# =============================================================================
# Skills — system prompts as plain Markdown files in app/skills/
# .pyfun [TOOL.xyz]: system_prompt_file = "my_skill.md"  (combined with system_prompt if both set)
# DATA only: the file is read as text and sent as system prompt. No code is ever executed
# or imported from app/skills/ — dropping a file there cannot run anything.
# =============================================================================
_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_SKILL_MAX_CHARS = 20000
_skill_cache: Dict[str, str] = {}


def _load_skill(name: str) -> str:
    """Read app/skills/<name> (.md/.txt only, no path tricks). Cached until restart."""
    name = (name or "").strip()
    if not name:
        return ""
    if name in _skill_cache:
        return _skill_cache[name]
    path = (_SKILLS_DIR / name).resolve()
    if _SKILLS_DIR not in path.parents or path.suffix.lower() not in (".md", ".txt"):
        raise ValueError(f"system_prompt_file '{name}' must be a .md/.txt file inside app/skills/")
    try:
        text = path.read_text(encoding="utf-8").strip()[:_SKILL_MAX_CHARS]
    except OSError:
        raise ValueError(f"system_prompt_file '{name}' not found in app/skills/") from None
    _skill_cache[name] = text
    return text


# =============================================================================
# Discord post (hub → Discord). Inbound (/discord route, /ask) lives in app.py.
# =============================================================================

async def _run_discord(content: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post `content` to Discord. mode: "auto" (default) | "webhook" | "bot".
    A channel_id override is only accepted if listed in [DISCORD] DISCORD_ALLOWED_CHANNELS
    (or equals DISCORD_DEFAULT_CHANNEL) — a hub client must not be able to make the bot
    write into arbitrary channels.
    """
    from . import discord_api
    cfg     = config.get_discord()
    allowed = {c.strip() for c in str(cfg.get("DISCORD_ALLOWED_CHANNELS", "")).split(",") if c.strip()}
    if cfg.get("DISCORD_DEFAULT_CHANNEL"):
        allowed.add(str(cfg["DISCORD_DEFAULT_CHANNEL"]).strip())

    channel = str(params.get("channel_id") or "").strip()
    if channel and channel not in allowed:
        return {"ok": False, "error": "channel_id not allowed — add it to DISCORD_ALLOWED_CHANNELS in .pyfun"}

    mode = str(params.get("mode") or "auto").lower()
    if mode == "webhook" or (mode == "auto" and not channel and discord_api.get_status()["webhook_set"]):
        r = await discord_api.send_webhook(str(content))
    else:
        r = await discord_api.send_message(channel, str(content))
    return {"ok": bool(r.get("ok")), "error": r.get("error"), "message_id": (r.get("data") or {}).get("id")}


# =============================================================================
# Response cache helper (opt-in per tool, backed by db_sync.tool_cache)
# =============================================================================

async def _cached(tool_name: str, key: str, tool_cfg: Dict, enabled: bool, call) -> str:
    """Run `call()` (async callable); with cache = "true" serve/store via db_sync. Never breaks the call."""
    if not enabled:
        return await call()
    try:
        ttl = int(tool_cfg.get("cache_ttl_sec", "3600"))
    except (TypeError, ValueError):
        ttl = 3600
    try:
        hit = await db_sync.cache_read(tool_name, key, max_age_sec=ttl)
        if hit is not None:
            logger.info(f"Tool '{tool_name}': cache hit.")
            return hit
    except Exception as e:
        logger.debug(f"Cache read skipped: {type(e).__name__}")
    result = await call()
    try:
        await db_sync.cache_write(tool_name, key, result)
    except Exception as e:
        logger.debug(f"Cache write skipped: {type(e).__name__}")
    return result


# =============================================================================
# Public API — used by mcp.py tool handlers
# =============================================================================

async def run(
    tool_name: str,
    prompt: Optional[str] = None,
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    max_results: int = 5,
    **params: Any,
) -> str:
    """
    Execute a tool by name.
    Reads tool config from registry, delegates to providers.py / db_sync.py.
    Applies system_prompt from .pyfun if defined.

    Args:
        tool_name:     Tool name as defined in .pyfun [TOOLS] (e.g. 'llm_complete').
        prompt:        User input / query string. For db tools this is the SQL query.
        provider_name: Override provider. Defaults to tool's default_provider in .pyfun.
        model:         Override model. Defaults to provider's default_model in .pyfun.
        max_tokens:    Max tokens for LLM response. Default: 1024.
        max_results:   Max results for search tools. Default: 5.
        **params:      Ignored extras — keeps /api calls with unknown keys from crashing.

    Returns:
        Tool response as plain text string.

    Raises:
        ValueError:   If tool_name is not found in registry or prompt is missing.
        RuntimeError: If all providers fail (propagated from providers.py).
    """
    tool_cfg = _registry.get(tool_name)
    if not tool_cfg:
        raise ValueError(f"Tool '{tool_name}' not found in registry or not active.")

    provider_type    = tool_cfg.get("provider_type", "llm")
    default_provider = provider_name or tool_cfg.get("default_provider", "")
    system_prompt    = tool_cfg.get("system_prompt", "")
    if tool_cfg.get("system_prompt_file"):
        system_prompt = f"{system_prompt}\n\n{_load_skill(tool_cfg['system_prompt_file'])}".strip()

    # Skill DBs (app/skills_db.py): core rules first, relevant user skills last. Opt-in (costs tokens).
    if provider_type == "llm":
        from . import skills_db
        scfg = config.get_skills()

        def _on(tool_key: str, global_key: str) -> bool:
            return str(tool_cfg.get(tool_key, scfg.get(global_key, "false"))).lower() == "true"

        if skills_db.is_ready():
            if _on("use_core", "SKILLS_INJECT_CORE") and skills_db.core_text():
                system_prompt = f"{skills_db.core_text()}\n\n{system_prompt}".strip()
            if _on("use_skills", "SKILLS_INJECT_RELEVANT"):
                ctx = await skills_db.context_for(
                    str(prompt or ""), int(scfg.get("SKILLS_TOP_K", "3")), int(scfg.get("SKILLS_MAX_CHARS", "3000")))
                if ctx:
                    system_prompt = f"{system_prompt}\n\n{ctx}".strip()

    # prompt ist für llm/search/db Pflicht — sauberer 400er statt TypeError/500er
    # persist ist die Ausnahme: ohne state_read_key braucht es einen prompt,
    # mit state_read_key liest es stattdessen aus hub_state (Gedächtnis-Pattern).
    needs_prompt = provider_type in ("llm", "search", "db", "discord") or \
        (provider_type == "skills" and tool_cfg.get("skills_action") == "search")
    if needs_prompt and (prompt is None or str(prompt).strip() == ""):
        raise ValueError(
            f"Tool '{tool_name}' requires a 'prompt' parameter (db: the SQL query, discord: the message)."
        )
    if provider_type == "persist" and not tool_cfg.get("state_read_key", "") \
            and (prompt is None or str(prompt).strip() == ""):
        raise ValueError(
            "persist_result requires 'prompt' (or set state_read_key in .pyfun [TOOL.persist_result])."
        )

    # --- Skill DBs (read-only): catalog / search ---
    if provider_type == "skills":
        from . import skills_db
        if tool_cfg.get("skills_action") == "catalog":
            return await skills_db.catalog(scope=str(params.get("scope") or "all"), tag=str(params.get("tag") or ""))
        return await skills_db.search(
            query=str(prompt or ""), top_k=params.get("top_k", 3),
            tag=str(params.get("tag") or ""), scope=str(params.get("scope") or "user"))

    # --- Discord post ---
    if provider_type == "discord":
        return await _run_discord(prompt, params)

    # --- Google Search Console (optional libs, see app/gsc.py) ---
    if provider_type == "gsc":
        from . import gsc
        return await gsc.query(
            property_url=str(params.get("property_url") or prompt or ""),
            days_back=params.get("days_back", 30),
            row_limit=params.get("row_limit", 10),
            dimension=params.get("dimension", "query"),
        )

    # Opt-in response cache per tool: cache = "true" (+ cache_ttl_sec) in .pyfun [TOOL.xyz]
    use_cache = str(tool_cfg.get("cache", "false")).lower() == "true"

    # --- LLM tools ---
    # system_prompt goes out as a REAL system prompt (no longer glued into the user prompt).
    # Only a model given (no provider) → provider is derived from .pyfun [MODELS].
    if provider_type == "llm":
        prov = provider_name or providers.provider_for_model(model) or tool_cfg.get("default_provider", "")
        return await _cached(
            tool_name, f"{prov}|{model or ''}|{max_tokens}|{system_prompt}|{prompt}", tool_cfg, use_cache,
            lambda: providers.llm_complete(
                prompt=prompt,
                provider_name=prov,
                model=model,
                max_tokens=max_tokens,
                system=system_prompt or None,
                tool_name=tool_name,
            ),
        )

    # --- Search tools ---
    # providers.search() ignores provider names that are not search providers
    # (e.g. a GUI sending its LLM provider along).
    if provider_type == "search":
        n_results = max_results or int(tool_cfg.get("default_results", "5"))
        return await _cached(
            tool_name, f"{default_provider}|{n_results}|{prompt}", tool_cfg, use_cache,
            lambda: providers.search(
                query=prompt,
                provider_name=default_provider,
                max_results=n_results,
            ),
        )

    # --- DB tools (read-only, delegated to db_sync when ready) ---
    if provider_type == "db":
        sql = prompt  # prompt ist hier die SQL-Query
        return await db_sync.query(sql)

    # --- Persist tools (Gedächtnis-Orchestrierung: SQLite-Queue → PostgreSQL) ---
    # NIEMALS direkt an PG schreiben — enqueue in die restart-safe Queue,
    # dann EINEN best-effort Flush-Versuch. Ohne DATABASE_URL = status "queued",
    # kein Crash. Der Background-Flusher (db_sync) retries später automatisch.
    if provider_type == "persist":
        key    = tool_cfg.get("state_read_key", "")
        table  = tool_cfg.get("target_table", "hub_results")
        source = str(params.get("source") or tool_name)
        # score: optionaler Slot für den Bullshit-Filter (Nutzer-eigenes Scoring).
        # Wird 1:1 durchgereicht — KEIN Tool hier interpretiert oder berechnet ihn.
        try:
            score = float(params["score"]) if params.get("score") is not None else None
        except (TypeError, ValueError):
            score = None
        data  = await db_sync.read(key) if key else {"prompt": prompt}
        depth = await db_sync.enqueue_persist(
            table, payload=data, source=source, model=model or "", score=score,
        )
        # best effort — waits at most PG_REQUEST_WAIT_SEC; a sleeping Neon never blocks the request
        flush = await db_sync.flush_persist_queue_bounded()
        if flush.get("flushed", 0) > 0:
            status, note = "persisted", "written to PostgreSQL"
        else:
            status = "queued"
            note = {
                "pg_slow":    "PostgreSQL is slow/waking up — stays queued (SQLite) and flushes in the background",
                "pg_backoff": "PostgreSQL unreachable a moment ago — stays queued (SQLite), retried automatically",
            }.get(flush.get("reason"), "restart-safe in SQLite — flushes to PostgreSQL when the bridge becomes active")
        return {
            "status":        status,
            "target_table":  table,
            "queue_depth":   depth,
            "flushed":       flush.get("flushed", 0),
            "note":          note,
        }

    # --- Unknown provider type ---
    logger.warning(f"Tool '{tool_name}' has unknown provider_type '{provider_type}' — skipped.")
    return f"Tool '{tool_name}' provider type '{provider_type}' is not yet implemented."


# =============================================================================
# Registry helpers — used by mcp.py and system tools
# =============================================================================

def get(tool_name: str) -> Dict[str, Any]:
    """
    Get full config dict for a tool.

    Args:
        tool_name: Tool name as defined in .pyfun [TOOLS].

    Returns:
        Tool config dict, or empty dict if not found.
    """
    return _registry.get(tool_name, {})


def get_description(tool_name: str) -> str:
    """
    Get the description of a tool (from .pyfun).

    Args:
        tool_name: Tool name as defined in .pyfun [TOOLS].

    Returns:
        Description string, or empty string if not found.
    """
    return _registry.get(tool_name, {}).get("description", "")


def get_system_prompt(tool_name: str) -> str:
    """
    Get the system_prompt of a tool (from .pyfun).
    Returns empty string if no system_prompt is defined.

    Args:
        tool_name: Tool name as defined in .pyfun [TOOLS].

    Returns:
        System prompt string, or empty string if not configured.
    """
    return _registry.get(tool_name, {}).get("system_prompt", "")


def get_timeout(tool_name: str) -> int:
    """
    Get the timeout in seconds for a tool (from .pyfun).

    Args:
        tool_name: Tool name as defined in .pyfun [TOOLS].

    Returns:
        Timeout in seconds (int). Defaults to 60 if not configured.
    """
    return int(_registry.get(tool_name, {}).get("timeout_sec", "60"))


def get_provider_type(tool_name: str) -> str:
    """
    Get the provider_type of a tool (llm | search | db | image | sandbox).

    Args:
        tool_name: Tool name as defined in .pyfun [TOOLS].

    Returns:
        Provider type string, or empty string if not found.
    """
    return _registry.get(tool_name, {}).get("provider_type", "")


def list_all() -> list:
    """
    List all active tool names from registry.

    Returns:
        List of active tool name strings.
    """
    return list(_registry.keys())


def list_by_type(provider_type: str) -> list:
    """
    List all active tools of a specific provider_type.

    Args:
        provider_type: e.g. 'llm', 'search', 'db', 'image', 'sandbox'.

    Returns:
        List of tool name strings matching the provider_type.
    """
    return [
        name for name, cfg in _registry.items()
        if cfg.get("provider_type", "") == provider_type
    ]


# =============================================================================
# Direct execution guard
# =============================================================================

if __name__ == "__main__":
    print("WARNING: Run via main.py → app.py, not directly.")
