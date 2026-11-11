# =============================================================================
# app/providers.py
# 09.03.2026 | updated 23.03.2026 | reworked 10.11.2026
# LLM + Search Provider Registry + Fallback Chain
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
# PROVIDER PRINCIPLE:
#   No key = no provider = no tool = no crash.
#   Server always starts, just with fewer providers.
#
# HOW TO ADD A NEW LLM PROVIDER:
#   OpenAI-compatible API (Mistral, OpenAI, xAI, Groq, Together, DeepSeek, ...):
#       → ONLY a [LLM_PROVIDER.xyz] block in app/.pyfun with type = "openai_compat"
#         + the key in .env. NO code needed. See examples in .pyfun.
#   Different API format (like Anthropic / Gemini):
#       1. Add a class below (subclass BaseProvider, implement complete())
#       2. Register it in _TYPE_CLASSES
#       3. Add the [LLM_PROVIDER.xyz] block in .pyfun with type = "<your type>"
#
# FALLBACK CHAIN:
#   Defined in .pyfun per provider via fallback_to field.
#   anthropic → fails → gemini → fails → openrouter → fails → RuntimeError
#   Visited set prevents infinite loops.
#   A model chosen explicitly applies to the FIRST provider only — every
#   fallback provider uses its own default_model (model IDs are provider-specific).
#
# LIMITS (from .pyfun [HUB_LIMITS]):
#   MAX_PARALLEL_REQUESTS → semaphore around every provider call
#   RETRY_COUNT / RETRY_DELAY_SEC → attempts per provider on 429/5xx/connect errors
#                                   (timeouts are NOT retried → straight to fallback)
#   MAX_TOKENS_CAP → hard ceiling for max_tokens per call (0 = off)
#   Per-model max_output_tokens (manual or auto from pricing) clamps further.
#
# USAGE / COST:
#   complete() returns (text, usage). llm_complete() logs usage + estimated cost
#   into db_sync (usage_log) after every successful call. Never breaks a call.
#
# SECURITY NOTE:
#   API keys are NEVER logged or included in exception messages.
#   All errors are sanitized before propagation — only HTTP status codes
#   and safe_url (query params stripped) are ever exposed in logs.
#
# CACHING NOTE:
#   Anthropic → prompt_caching (cache_control: ephemeral) for long system/user prompts.
#     Enable per provider in .pyfun: supports_cache = "true"
#   Gemini → implicit caching (automatic, server-side). supports_cache = log hint only.
#
# DEPENDENCY CHAIN (app/* only, no fundaments!):
#   config.py    → parses app/.pyfun — single source of truth
#   providers.py → LLM + Search registry + fallback chain
#   tools.py     → calls providers.llm_complete() / providers.search()
#   mcp.py       → calls providers.list_active_llm() / list_active_search()
# =============================================================================

import os
import re
import time
import asyncio
import logging
import httpx
from typing import Optional, Tuple, Dict, Any

from . import config
from . import models as models_mod
from . import pricing
from . import db_sync

logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("providers")


# =============================================================================
# SECTION 0 — Errors, Limits, Retry
# =============================================================================

class ProviderError(RuntimeError):
    """Sanitized provider error. RuntimeError subclass → app.py maps it to 502."""
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504, 529}


def _limit(key: str, default, cast=int):
    """Read one value from .pyfun [HUB_LIMITS] with safe fallback."""
    try:
        return cast(config.get_limits().get(key, default))
    except (TypeError, ValueError):
        return cast(default)


_sem: Optional[asyncio.Semaphore] = None


def _get_sem() -> asyncio.Semaphore:
    """One global semaphore (MAX_PARALLEL_REQUESTS) — created lazily inside the loop."""
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(max(1, _limit("MAX_PARALLEL_REQUESTS", 5)))
    return _sem


def _is_transient(e: Exception) -> bool:
    """429/5xx and connect errors are worth a retry. Timeouts are NOT (60s x N = forever)."""
    if isinstance(e, ProviderError):
        return e.status in _TRANSIENT_STATUS
    return isinstance(e, httpx.ConnectError)


async def _with_retry(name: str, call):
    """
    Run `call()` (async callable) under the global semaphore.
    Attempts = RETRY_COUNT (incl. the first), flat RETRY_DELAY_SEC between them.
    """
    attempts = max(1, _limit("RETRY_COUNT", 3))
    delay    = max(0.0, _limit("RETRY_DELAY_SEC", 2, float))
    for i in range(1, attempts + 1):
        try:
            async with _get_sem():
                return await call()
        except Exception as e:
            if i >= attempts or not _is_transient(e):
                raise
            logger.info(f"'{name}' transient error ({type(e).__name__}) — retry {i}/{attempts - 1} in {delay}s")
            await asyncio.sleep(delay)


def _usage(tokens_in: Any, tokens_out: Any) -> Dict[str, Optional[int]]:
    """Normalize token counts to int|None."""
    def _i(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return {"input": _i(tokens_in), "output": _i(tokens_out)}


# =============================================================================
# SECTION 1 — Base Provider
# Shared HTTP logic — implemented ONCE, reused by all providers.
# =============================================================================

class BaseProvider:
    """
    Base class for all LLM providers.
    Subclasses only implement complete() — HTTP logic lives here.
    complete() returns (text, usage) with usage = {"input": int|None, "output": int|None}.
    """
    def __init__(self, name: str, cfg: dict):
        self.name          = name
        self.cfg           = cfg
        self.key           = os.getenv(cfg.get("env_key", ""))
        self.base_url      = cfg.get("base_url", "")
        self.fallback      = cfg.get("fallback_to", "")
        self.timeout       = int(config.get_limits().get("REQUEST_TIMEOUT_SEC", "60"))
        self.model         = cfg.get("default_model", "")
        self.supports_cache = cfg.get("supports_cache", "false").lower() == "true"
        # Safe key hint for debug logs — never log the full key
        self._key_hint = (
            f"{self.key[:4]}...{self.key[-4:]}"
            if self.key and len(self.key) > 8
            else "***"
        )

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024,
                       system: str = None) -> Tuple[str, Dict[str, Optional[int]]]:
        """Override in each provider subclass."""
        raise NotImplementedError

    async def _post(self, url: str, headers: dict, payload: dict) -> dict:
        """
        Shared HTTP POST — used by all providers.
        Raises ProviderError with sanitized message on non-2xx responses.
        API keys are never included in raised exceptions or log output.
        """
        safe_url = url.split("?")[0]  # strip query params (may contain API keys)
        logger.debug(f"POST → {safe_url}")
        async with httpx.AsyncClient() as client:
            r = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                # Sanitize: only status code + safe_url, never headers or body
                raise ProviderError(
                    f"HTTP {e.response.status_code} from {safe_url}",
                    e.response.status_code,
                ) from None
            return r.json()


# =============================================================================
# SECTION 2 — LLM Provider Implementations
# Only the API-specific parsing logic differs per provider.
# =============================================================================

# --- OpenAI-compatible (GENERIC — no code needed for new providers) -----------
class OpenAICompatProvider(BaseProvider):
    """
    Generic OpenAI-compatible /chat/completions provider.
    Covers smollm, openrouter, huggingface, mistral, openai, xai, groq, ...
    Everything provider-specific lives in the .pyfun block:

        [LLM_PROVIDER.mistral]
        active        = "true"
        type          = "openai_compat"
        base_url      = "https://api.mistral.ai/v1"
        env_key       = "MISTRAL_API_KEY"
        default_model = "mistral-small-latest"
        fallback_to   = ""
        # optional:
        # auth_header = "Authorization"   (default)
        # auth_scheme = "Bearer"          (default; "" = send the raw key)
        # chat_path   = "/chat/completions" (default)
        # header.X-Foo = "{key}"          extra header; {key} = API key,
        #                                 {env:NAME|fallback} = ENV value
        [LLM_PROVIDER.mistral_END]
    """
    _PLACEHOLDER = re.compile(r"\{(key|env:([A-Za-z0-9_]+)(?:\|([^}]*))?)\}")

    def __init__(self, name: str, cfg: dict):
        super().__init__(name, cfg)
        self.auth_header = cfg.get("auth_header", "Authorization")
        self.auth_scheme = cfg.get("auth_scheme", "Bearer")
        self.chat_path   = cfg.get("chat_path", "/chat/completions")

    def _fill(self, value: str) -> str:
        def sub(m):
            if m.group(1) == "key":
                return self.key or ""
            return os.getenv(m.group(2)) or (m.group(3) or "")
        return self._PLACEHOLDER.sub(sub, value)

    def _headers(self) -> dict:
        headers = {
            self.auth_header: f"{self.auth_scheme} {self.key}".strip(),
            "content-type":   "application/json",
        }
        for k, v in self.cfg.items():
            if k.startswith("header."):
                headers[k[len("header."):]] = self._fill(v)
        return headers

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024,
                       system: str = None):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        data = await self._post(
            f"{self.base_url}{self.chat_path}",
            headers=self._headers(),
            payload={
                "model":      model or self.model,
                "max_tokens": max_tokens,
                "messages":   messages,
            },
        )
        text = data["choices"][0]["message"]["content"]
        if text is None:
            raise ProviderError("empty content in response")
        u = data.get("usage") or {}
        return text, _usage(u.get("prompt_tokens"), u.get("completion_tokens"))


# --- Anthropic ----------------------------------------------------------------
class AnthropicProvider(BaseProvider):
    """
    Anthropic Claude API — Messages endpoint.

    Prompt Caching (supports_cache = "true" in .pyfun):
        Uses cache_control: ephemeral on system prompt and long user prompts.
        Cache TTL: 5 minutes, extended on each cache hit.
        Min tokens to cache: ~1024 (Anthropic requirement).
        Cost: cache write ~25% more, cache read ~90% less than normal input.

    Usage: input = input + cache_creation + cache_read tokens (upper bound for cost).

    .pyfun block:
        [LLM_PROVIDER.anthropic]
        active             = "true"
        type               = "anthropic"
        base_url           = "https://api.anthropic.com/v1"
        env_key            = "ANTHROPIC_API_KEY"
        api_version_header = "2023-06-01"
        default_model      = "claude-haiku-5-5"
        supports_cache     = "true"
        fallback_to        = "gemini"
        [LLM_PROVIDER.anthropic_END]
    """

    # Rough chars-per-token estimate — avoids importing tiktoken in sandbox
    _CHARS_PER_TOKEN = 4
    _CACHE_MIN_TOKENS = 1024

    def __init__(self, name: str, cfg: dict):
        super().__init__(name, cfg)
        self.api_version = cfg.get("api_version_header", "2023-06-01")

    def _is_cacheable(self, text: str) -> bool:
        """Estimate if text is long enough to benefit from caching."""
        return len(text) >= self._CACHE_MIN_TOKENS * self._CHARS_PER_TOKEN

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024,
                       system: str = None):
        headers = {
            "x-api-key":         self.key,
            "anthropic-version": self.api_version,
            "content-type":      "application/json",
        }

        # --- Build user content (cache_control if enabled + long enough) ---
        if self.supports_cache and self._is_cacheable(prompt):
            user_content = [{"type": "text", "text": prompt,
                             "cache_control": {"type": "ephemeral"}}]
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"
            logger.debug("Anthropic: prompt cache_control applied to user message.")
        else:
            user_content = prompt  # short prompt — plain string, no overhead

        payload = {
            "model":      model or self.model,
            "max_tokens": max_tokens,
            "messages":   [{"role": "user", "content": user_content}],
        }

        # --- Optional system prompt with cache_control ---
        if system:
            if self.supports_cache and self._is_cacheable(system):
                payload["system"] = [{"type": "text", "text": system,
                                      "cache_control": {"type": "ephemeral"}}]
                headers["anthropic-beta"] = "prompt-caching-2024-07-31"
                logger.debug("Anthropic: prompt cache_control applied to system prompt.")
            else:
                payload["system"] = system

        data = await self._post(f"{self.base_url}/messages", headers, payload)
        text = data["content"][0]["text"]
        u = data.get("usage") or {}
        tin = None
        if u:
            tin = sum(int(u.get(k) or 0) for k in
                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        return text, _usage(tin, u.get("output_tokens"))


# --- Gemini ------------------------------------------------------------------
class GeminiProvider(BaseProvider):
    """
    Google Gemini API — generateContent endpoint.

    Implicit Caching (always active on Gemini side, no code needed).
    Explicit Context Caching is NOT implemented (only worth it for 32k+ static contexts).

    .pyfun block:
        [LLM_PROVIDER.gemini]
        active         = "true"
        type           = "gemini"
        base_url       = "https://generativelanguage.googleapis.com/v1beta"
        env_key        = "GEMINI_API_KEY"
        default_model  = "gemini-2.5-flash"
        supports_cache = "true"
        fallback_to    = "openrouter"
        [LLM_PROVIDER.gemini_END]
    """

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024,
                       system: str = None):
        m        = model or self.model
        safe_url = f"{self.base_url}/models/{m}:generateContent"

        if self.supports_cache:
            logger.debug(f"Gemini: implicit caching active for model {m} (server-side, automatic).")

        body = {
            "contents":         [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        async with httpx.AsyncClient() as client:
            r = await client.post(
                safe_url,
                params={"key": self.key},  # key in query param, never in logs
                json=body,
                timeout=self.timeout,
            )
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise ProviderError(
                    f"HTTP {e.response.status_code} from {safe_url}",
                    e.response.status_code,
                ) from None
            data = r.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        meta = data.get("usageMetadata") or {}
        return text, _usage(meta.get("promptTokenCount"), meta.get("candidatesTokenCount"))


# =============================================================================
# SECTION 3 — Provider Registry
# Built from .pyfun [LLM_PROVIDERS] at initialize().
# Handler is chosen by the `type` field of the block (default: the block name).
#   type = "openai_compat" → OpenAICompatProvider (generic, no code per provider)
#   type = "anthropic"     → AnthropicProvider
#   type = "gemini"        → GeminiProvider
# New API format = new class above + one line here.
# =============================================================================

_TYPE_CLASSES = {
    "openai_compat": OpenAICompatProvider,
    "anthropic":     AnthropicProvider,
    "gemini":        GeminiProvider,
}

_registry: dict = {}


def initialize() -> None:
    """
    Build provider registries from .pyfun [LLM_PROVIDERS] + [SEARCH_PROVIDERS].
    Called once by mcp.py during startup sequence.
    Skips providers with missing ENV keys — no crash, just fewer tools.
    """
    global _registry
    active = config.get_active_llm_providers()

    for name, cfg in active.items():
        env_key = cfg.get("env_key", "")
        if not env_key or not os.getenv(env_key):
            logger.info(f"Provider '{name}' skipped — ENV key not set.")
            continue
        ptype = (cfg.get("type") or name).strip().lower()
        cls = _TYPE_CLASSES.get(ptype)
        if not cls:
            logger.info(f"Provider '{name}' has no handler (type '{ptype}') — skipped.")
            continue
        _registry[name] = cls(name, cfg)
        cache_hint = " [cache: ON]" if cfg.get("supports_cache", "false") == "true" else ""
        logger.info(f"Provider registered: {name} (type: {ptype}){cache_hint}")

    # Search providers — same key-gating principle
    _initialize_search()


# =============================================================================
# SECTION 4 — LLM Execution + Fallback Chain
# =============================================================================

def provider_for_model(model: str) -> Optional[str]:
    """
    Provider that owns `model` according to .pyfun [MODELS] (provider field).
    None if the model is unknown → caller keeps its default provider.
    """
    if not model:
        return None
    entry = models_mod.get(model) or {}
    return entry.get("provider") or None


def _clamp_tokens(model: str, max_tokens: int) -> int:
    """Hard cap (MAX_TOKENS_CAP) + per-model max_output_tokens (manual or auto-pricing)."""
    try:
        n = max(1, int(max_tokens))
    except (TypeError, ValueError):
        n = 1024
    cap = _limit("MAX_TOKENS_CAP", 8192)
    if cap > 0:
        n = min(n, cap)
    lim = pricing.max_output_for(model)
    if lim:
        n = min(n, lim)
    return n


async def _record_usage(tool: str, provider: str, model: str, usage: dict, latency_ms: int) -> None:
    """Log usage + estimated cost. NEVER raises — accounting must not break a call."""
    try:
        tin, tout = usage.get("input"), usage.get("output")
        cost = None
        if tin is not None or tout is not None:
            cost = await pricing.cost_for(model, tin, tout)
        await db_sync.log_usage(tool, provider, model, tin, tout, cost, latency_ms)
        cost_txt = f"${cost:.6f}" if cost is not None else "n/a"
        logger.info(f"Usage: {provider}/{model} in={tin} out={tout} cost≈{cost_txt} ({latency_ms} ms)")
    except Exception as e:
        logger.debug(f"Usage logging skipped: {type(e).__name__}")


async def llm_complete(
    prompt: str,
    provider_name: str = None,
    model: str = None,
    max_tokens: int = 1024,
    system: str = None,
    tool_name: str = "llm_complete",
) -> str:
    """
    Send prompt to LLM provider with automatic fallback chain.
    Fallback order is defined in .pyfun via fallback_to field.
    Raises RuntimeError if all providers in the chain fail.

    Args:
        prompt:        Input text to send to the model.
        provider_name: Provider override. Unknown / non-LLM names are ignored.
                       If only `model` is given, the provider is derived from [MODELS].
        model:         Model override — applies to the FIRST provider only.
        max_tokens:    Max tokens in response (clamped, see header).
        system:        Optional system prompt — sent as real system prompt to every provider.
        tool_name:     Only used for the usage log.

    Returns:
        "[provider] text"
    """
    model = (model or "").strip() or None
    if provider_name and provider_name not in config.get_llm_providers():
        logger.debug(f"'{provider_name}' is not an LLM provider — using default.")
        provider_name = None
    if not provider_name:
        provider_name = provider_for_model(model)
    if not provider_name:
        tools_cfg     = config.get_active_tools()
        provider_name = tools_cfg.get("llm_complete", {}).get("default_provider", "anthropic")

    visited = set()
    current = provider_name
    first   = True

    while current and current not in visited:
        visited.add(current)
        provider  = _registry.get(current)
        use_model = model if first else None   # explicit model belongs to the first provider only
        first     = False

        if not provider:
            logger.warning(f"Provider '{current}' not in registry — trying fallback.")
        else:
            m  = use_model or provider.model
            mt = _clamp_tokens(m, max_tokens)
            try:
                t0 = time.monotonic()
                text, usage = await _with_retry(
                    current,
                    lambda p=provider, um=use_model, n=mt: p.complete(prompt, um, n, system=system),
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.info(f"Response from provider: '{current}'")
                await _record_usage(tool_name, current, m, usage, latency_ms)
                return f"[{current}] {text}"
            except Exception as e:
                # Log only exception type + sanitized message — never raw headers/keys/bodies
                logger.warning(
                    f"Provider '{current}' failed: {type(e).__name__}: {e} — trying fallback."
                )

        cfg     = config.get_active_llm_providers().get(current, {})
        current = cfg.get("fallback_to", "")

    raise RuntimeError("All providers failed — no fallback available.")


# Alias — used internally by tools.py
complete = llm_complete


# =============================================================================
# SECTION 5 — Search Execution
# Same pattern as LLM providers: class + _SEARCH_PROVIDER_CLASSES + fallback.
# Adding a search provider = add class + register + .pyfun block. Nothing else.
# =============================================================================

class BaseSearchProvider:
    """
    Base class for all search providers.
    Subclasses only implement search() — HTTP logic lives here.
    """
    def __init__(self, name: str, cfg: dict):
        self.name        = name
        self.key         = os.getenv(cfg.get("env_key", ""))
        self.base_url    = cfg.get("base_url", "")
        self.fallback    = cfg.get("fallback_to", "")
        self.timeout     = int(config.get_limits().get("SEARCH_TIMEOUT_SEC", "30"))
        self.default_results = int(cfg.get("default_results", "5"))
        self.max_results     = int(cfg.get("max_results", "20"))
        self._key_hint = (
            f"{self.key[:4]}...{self.key[-4:]}"
            if self.key and len(self.key) > 8
            else "***"
        )

    async def search(self, query: str, max_results: int) -> str:
        """Override in each search provider subclass."""
        raise NotImplementedError

    async def _get(self, url: str, headers: dict, params: dict) -> dict:
        """Shared HTTP GET with sanitized errors (never leak keys/bodies)."""
        safe_url = url.split("?")[0]
        async with httpx.AsyncClient() as client:
            r = await client.get(url, headers=headers, params=params, timeout=self.timeout)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise ProviderError(f"HTTP {e.response.status_code} from {safe_url}", e.response.status_code) from None
            return r.json()

    async def _post(self, url: str, headers: dict, payload: dict) -> dict:
        """Shared HTTP POST with sanitized errors."""
        safe_url = url.split("?")[0]
        async with httpx.AsyncClient() as client:
            r = await client.post(url, headers=headers, json=payload, timeout=self.timeout)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise ProviderError(f"HTTP {e.response.status_code} from {safe_url}", e.response.status_code) from None
            return r.json()

    def _clip(self, max_results: int) -> int:
        """Clamp requested results into [1, max_results]."""
        try:
            n = int(max_results)
        except (TypeError, ValueError):
            n = self.default_results
        return max(1, min(n, self.max_results))


# --- Brave Search ---------------------------------------------------------------
class BraveSearchProvider(BaseSearchProvider):
    """
    Brave Search API — web search endpoint.
    Auth via X-Subscription-Token header (NOT Bearer!).

    .pyfun block:
        [SEARCH_PROVIDER.brave]
        active          = "true"
        base_url        = "https://api.search.brave.com/res/v1/web/search"
        env_key         = "BRAVE_API_KEY"
        default_results = "5"
        max_results     = "20"
        fallback_to     = "tavily"
        [SEARCH_PROVIDER.brave_END]
    """

    async def search(self, query: str, max_results: int = 5) -> str:
        data = await self._get(
            self.base_url,
            headers={
                "X-Subscription-Token": self.key,
                "Accept":              "application/json",
                "Accept-Encoding":     "gzip",
            },
            params={
                "q":     query,
                "count": self._clip(max_results),
            },
        )
        results = data.get("web", {}).get("results", [])
        if not results:
            raise RuntimeError("Brave returned no results")
        lines = [f"[{self.name}] Search results for: '{query}'"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {r.get('description', '')}")
        return "\n\n".join(lines)


# --- Tavily ---------------------------------------------------------------------
class TavilySearchProvider(BaseSearchProvider):
    """
    Tavily Search API — AI-optimized web search.
    include_answer = "true" in .pyfun → adds AI-synthesized answer on top.

    .pyfun block:
        [SEARCH_PROVIDER.tavily]
        active          = "true"
        base_url        = "https://api.tavily.com/search"
        env_key         = "TAVILY_API_KEY"
        default_results = "5"
        max_results     = "10"
        include_answer  = "true"
        fallback_to     = ""
        [SEARCH_PROVIDER.tavily_END]
    """

    def __init__(self, name: str, cfg: dict):
        super().__init__(name, cfg)
        self.include_answer = cfg.get("include_answer", "false").lower() == "true"

    async def search(self, query: str, max_results: int = 5) -> str:
        data = await self._post(
            self.base_url,
            headers={"Content-Type": "application/json"},
            payload={
                "api_key":       self.key,   # Tavily takes the key in the payload
                "query":         query,
                "max_results":   self._clip(max_results),
                "include_answer": self.include_answer,
            },
        )
        results = data.get("results", [])
        if not results and not data.get("answer"):
            raise RuntimeError("Tavily returned no results")
        lines = [f"[{self.name}] Search results for: '{query}'"]
        if self.include_answer and data.get("answer"):
            lines.append(f"AI answer: {data['answer']}")
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {r.get('content', '')}")
        return "\n\n".join(lines)


# =============================================================================
# Search Registry — built from .pyfun [SEARCH_PROVIDERS] at initialize()
# =============================================================================

_SEARCH_PROVIDER_CLASSES = {
    "brave":  BraveSearchProvider,
    "tavily": TavilySearchProvider,
    # "serper": SerperSearchProvider,   # ← uncomment to activate
}

_search_registry: dict = {}


def _initialize_search() -> None:
    """
    Build search provider registry from .pyfun [SEARCH_PROVIDERS].
    Called inside initialize() — same key-gating as LLM providers:
    no ENV key = provider skipped, no crash.
    """
    global _search_registry
    for name, cfg in config.get_active_search_providers().items():
        env_key = cfg.get("env_key", "")
        if not env_key or not os.getenv(env_key):
            logger.info(f"Search provider '{name}' skipped — ENV key not set.")
            continue
        cls = _SEARCH_PROVIDER_CLASSES.get(name)
        if not cls:
            logger.info(f"Search provider '{name}' has no handler yet — skipped.")
            continue
        _search_registry[name] = cls(name, cfg)
        logger.info(f"Search provider registered: {name}")


async def search(
    query: str,
    provider_name: str = None,
    max_results: int = 5,
) -> str:
    """
    Search the web via configured search provider with automatic fallback chain.
    Fallback order is defined in .pyfun via fallback_to field.
    Raises RuntimeError if all providers in the chain fail.

    Args:
        query:         Search query string.
        provider_name: Provider name override (e.g. 'brave', 'tavily').
                       Defaults to default_provider from .pyfun [TOOL.web_search].
        max_results:   Maximum number of results. Default: 5.

    Returns:
        Formatted search results as plain text string.
    """
    if provider_name and provider_name not in config.get_search_providers():
        logger.debug(f"'{provider_name}' is not a search provider — using default.")
        provider_name = None
    if not provider_name:
        tools_cfg     = config.get_active_tools()
        provider_name = tools_cfg.get("web_search", {}).get("default_provider", "brave")

    visited = set()
    current = provider_name

    while current and current not in visited:
        visited.add(current)
        provider = _search_registry.get(current)

        if not provider:
            logger.warning(f"Search provider '{current}' not in registry — trying fallback.")
        else:
            try:
                result = await _with_retry(current, lambda p=provider: p.search(query, max_results))
                logger.info(f"Search response from provider: '{current}'")
                return result
            except Exception as e:
                logger.warning(
                    f"Search provider '{current}' failed: {type(e).__name__}: {e} — trying fallback."
                )

        cfg     = config.get_active_search_providers().get(current, {})
        current = cfg.get("fallback_to", "")

    raise RuntimeError("All search providers failed — no fallback available.")


# =============================================================================
# SECTION 6 — Registry Helpers
# Used by mcp.py for tool registration decisions.
# =============================================================================

def list_active_llm() -> list:
    """Returns list of active LLM provider names."""
    return list(_registry.keys())


def list_active_search() -> list:
    """Returns list of active search provider names."""
    return list(_search_registry.keys())


def get(name: str) -> BaseProvider:
    """Get a specific provider instance by name."""
    return _registry.get(name)


# =============================================================================
# Direct execution guard
# =============================================================================

if __name__ == "__main__":
    print("WARNING: Run via main.py → app.py, not directly.")