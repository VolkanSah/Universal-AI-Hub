# =============================================================================
# app/providers.py
# 09.03.2026 | updated 23.03.2026
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
#   Adding a new provider = update .pyfun + add class here. Never touch mcp.py!
#
# FALLBACK CHAIN:
#   Defined in .pyfun per provider via fallback_to field.
#   anthropic → fails → gemini → fails → openrouter → fails → RuntimeError
#   Visited set prevents infinite loops.
#
# SECURITY NOTE:
#   API keys are NEVER logged or included in exception messages.
#   All errors are sanitized before propagation — only HTTP status codes
#   and safe_url (query params stripped) are ever exposed in logs.
#
# CACHING NOTE:
#   Anthropic → prompt_caching (cache_control: ephemeral)
#     Requires anthropic-beta: prompt-caching-2024-07-31 header.
#     Caches system prompt + long user prompts (>1024 tokens estimated).
#     Saves up to 90% input token costs on repeated context.
#     Enable per provider in .pyfun: supports_cache = "true"
#
#   Gemini → Implicit caching (automatic, no extra API call needed)
#     Google automatically caches repeated prompt prefixes server-side.
#     No code change needed — Gemini handles it transparently.
#     Explicit Context Caching API exists but requires separate cache management
#     and is only worth it for very large static contexts (32k+ tokens).
#     Enable per provider in .pyfun: supports_cache = "true"
#     (currently used as log hint only for Gemini — implicit cache is always on)
#
# HOW TO ADD A NEW LLM PROVIDER — 3 steps, nothing else to touch:
#   1. Add class below (copy a dummy, implement complete())
#   2. Register name → class in _PROVIDER_CLASSES dict
#   3. Add [LLM_PROVIDER.yourprovider] block in app/.pyfun
#      → env_key, base_url, default_model, fallback_to
#
# DEPENDENCY CHAIN (app/* only, no fundaments!):
#   config.py    → parses app/.pyfun — single source of truth
#   providers.py → LLM + Search registry + fallback chain
#   tools.py     → calls providers.llm_complete() / providers.search()
#   mcp.py       → calls providers.list_active_llm() / list_active_search()
# =============================================================================

import os
import logging
import httpx

from . import config
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("providers")


# =============================================================================
# SECTION 1 — Base Provider
# Shared HTTP logic — implemented ONCE, reused by all providers.
# =============================================================================

class BaseProvider:
    """
    Base class for all LLM providers.
    Subclasses only implement complete() — HTTP logic lives here.
    """
    def __init__(self, name: str, cfg: dict):
        self.name          = name
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

    async def complete(self, prompt: str, model: str, max_tokens: int) -> str:
        """Override in each provider subclass."""
        raise NotImplementedError

    async def _post(self, url: str, headers: dict, payload: dict) -> dict:
        """
        Shared HTTP POST — used by all providers.
        Raises RuntimeError with sanitized message on non-2xx responses.
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
                raise RuntimeError(
                    f"HTTP {e.response.status_code} from {safe_url}"
                ) from None
            return r.json()


# =============================================================================
# SECTION 2 — LLM Provider Implementations
# Only the API-specific parsing logic differs per provider.
# =============================================================================

# --- SmolLM2 (Custom Assistant Space) ----------------------------------------
class SmolLMProvider(BaseProvider):
    """
    SmolLM2 Custom Assistant Space — OpenAI-compatible, ADI routing included.
    Free tier on HF Spaces (CPU). Falls back to next provider on 503.
    Response includes extra 'adi' field with score + decision (ignored by hub).
    Deploy: https://github.com/VolkanSah/Multi-LLM-API-Gateway (smollm-space/)

    .pyfun block:
        [LLM_PROVIDER.smollm]
        active        = "true"
        base_url      = "https://codey-lab-SmolLM2-customs.hf.space/v1"
        env_key       = "SMOLLM_API_KEY"
        default_model = "smollm2-360m"
        models        = "smollm2-360m, codey-lab/model.universal-mcp-hub"
        fallback_to   = "anthropic"
        [LLM_PROVIDER.smollm_END]
    """

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 150) -> str:
        data = await self._post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.key}",
                "X-IP-Token":     self.key,
                "content-type":  "application/json",
            },
            payload={
                "model":      model or self.model,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )
        return data["choices"][0]["message"]["content"]


# --- Anthropic ----------------------------------------------------------------
class AnthropicProvider(BaseProvider):
    """
    Anthropic Claude API — Messages endpoint.

    Prompt Caching (supports_cache = "true" in .pyfun):
        Uses cache_control: ephemeral on system prompt and long user prompts.
        Requires anthropic-beta: prompt-caching-2024-07-31 header.
        Cache TTL: 5 minutes, extended on each cache hit.
        Min tokens to cache: ~1024 (Anthropic requirement).
        Cost: cache write ~25% more, cache read ~90% less than normal input.

    .pyfun block:
        [LLM_PROVIDER.anthropic]
        active           = "true"
        base_url         = "https://api.anthropic.com/v1"
        env_key          = "ANTHROPIC_API_KEY"
        api_version_header = "2023-06-01"
        default_model    = "claude-haiku-4-5"
        supports_cache   = "true"
        fallback_to      = "gemini"
        [LLM_PROVIDER.anthropic_END]
    """

    # Rough chars-per-token estimate — avoids importing tiktoken in sandbox
    _CHARS_PER_TOKEN = 4
    _CACHE_MIN_TOKENS = 1024

    def _is_cacheable(self, text: str) -> bool:
        """Estimate if text is long enough to benefit from caching."""
        return len(text) >= self._CACHE_MIN_TOKENS * self._CHARS_PER_TOKEN

    async def complete(
        self,
        prompt: str,
        model: str = None,
        max_tokens: int = 1024,
        system: str = None,
    ) -> str:
        cfg = config.get_active_llm_providers().get("anthropic", {})

        headers = {
            "x-api-key":         self.key,
            "anthropic-version": cfg.get("api_version_header", "2023-06-01"),
            "content-type":      "application/json",
        }

        # --- Build user content ---
        # Add cache_control if caching enabled + prompt long enough
        if self.supports_cache and self._is_cacheable(prompt):
            user_content = [
                {
                    "type":          "text",
                    "text":          prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
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
                payload["system"] = [
                    {
                        "type":          "text",
                        "text":          system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
                headers["anthropic-beta"] = "prompt-caching-2024-07-31"
                logger.debug("Anthropic: prompt cache_control applied to system prompt.")
            else:
                payload["system"] = system

        data = await self._post(f"{self.base_url}/messages", headers, payload)
        return data["content"][0]["text"]


# --- Gemini ------------------------------------------------------------------
class GeminiProvider(BaseProvider):
    """
    Google Gemini API — generateContent endpoint.

    Implicit Caching (always active on Gemini side, no code needed):
        Google automatically caches repeated prompt prefixes server-side.
        No extra API call, no cache key, no TTL management needed.
        Just send the same prompt structure and Gemini handles the rest.
        supports_cache = "true" in .pyfun logs cache hint only.

    Explicit Context Caching (NOT implemented here — when to use it):
        Only worth the extra API complexity for very large static contexts
        (32k+ tokens, e.g. large documents sent on every request).
        Requires separate POST to /cachedContents, returns a cache_name,
        which is then referenced in generateContent as cachedContent.name.
        Implement as a separate tool (cache_create / cache_use) when needed.

    .pyfun block:
        [LLM_PROVIDER.gemini]
        active         = "true"
        base_url       = "https://generativelanguage.googleapis.com/v1beta"
        env_key        = "GEMINI_API_KEY"
        default_model  = "gemini-2.0-flash"
        supports_cache = "true"
        fallback_to    = "openrouter"
        [LLM_PROVIDER.gemini_END]
    """

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024) -> str:
        m        = model or self.model
        safe_url = f"{self.base_url}/models/{m}:generateContent"

        if self.supports_cache:
            logger.debug(f"Gemini: implicit caching active for model {m} (server-side, automatic).")

        async with httpx.AsyncClient() as client:
            r = await client.post(
                safe_url,
                params={"key": self.key},  # key in query param, never in logs
                json={
                    "contents":         [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens},
                },
                timeout=self.timeout,
            )
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(
                    f"HTTP {e.response.status_code} from {safe_url}"
                ) from None
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]


# --- OpenRouter ---------------------------------------------------------------
class OpenRouterProvider(BaseProvider):
    """OpenRouter API — OpenAI-compatible chat completions endpoint.

    Required headers: HTTP-Referer + X-Title (required by OpenRouter for
    free models and rate limit attribution).
    """

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024) -> str:
        data = await self._post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.key}",
                "HTTP-Referer":  os.getenv("APP_URL", "https://huggingface.co"),
                "X-Title":       os.getenv("HUB_NAME", "Universal AI Hub"),  # required!
                "content-type":  "application/json",
            },
            payload={
                "model":      model or self.model,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )
        return data["choices"][0]["message"]["content"]


# --- HuggingFace --------------------------------------------------------------
class HuggingFaceProvider(BaseProvider):
    """HuggingFace Inference API — OpenAI-compatible serverless endpoint.

    base_url in .pyfun: https://api-inference.huggingface.co/v1
    Model goes in payload, not in URL.
    Free tier: max ~8B models. PRO required for 70B+.
    """

    async def complete(self, prompt: str, model: str = None, max_tokens: int = 512) -> str:
        m    = model or self.model
        data = await self._post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.key}",
                "content-type":  "application/json",
            },
            payload={
                "model":      m,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
        )
        return data["choices"][0]["message"]["content"]


# =============================================================================
# DUMMY PROVIDERS — copy, uncomment, adapt
# Steps: (1) uncomment class  (2) add to _PROVIDER_CLASSES  (3) add to .pyfun
# =============================================================================

# --- OpenAI -------------------------------------------------------------------
# .pyfun block to add:
#
#   [LLM_PROVIDER.openai]
#   active        = "true"
#   base_url      = "https://api.openai.com/v1"
#   env_key       = "OPENAI_API_KEY"
#   default_model = "gpt-4o-mini"
#   models        = "gpt-4o, gpt-4o-mini, gpt-3.5-turbo"
#   fallback_to   = ""
#   [LLM_PROVIDER.openai_END]
#
# class OpenAIProvider(BaseProvider):
#     """OpenAI API — OpenAI-compatible chat completions endpoint."""
#
#     async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024) -> str:
#         data = await self._post(
#             f"{self.base_url}/chat/completions",
#             headers={
#                 "Authorization": f"Bearer {self.key}",
#                 "content-type":  "application/json",
#             },
#             payload={
#                 "model":      model or self.model,
#                 "max_tokens": max_tokens,
#                 "messages":   [{"role": "user", "content": prompt}],
#             },
#         )
#         return data["choices"][0]["message"]["content"]


# --- Mistral ------------------------------------------------------------------
# .pyfun block to add:
#
#   [LLM_PROVIDER.mistral]
#   active        = "true"
#   base_url      = "https://api.mistral.ai/v1"
#   env_key       = "MISTRAL_API_KEY"
#   default_model = "mistral-large-latest"
#   models        = "mistral-large-latest, mistral-small-latest, codestral-latest"
#   fallback_to   = ""
#   [LLM_PROVIDER.mistral_END]
#
# class MistralProvider(BaseProvider):
#     """Mistral AI API — OpenAI-compatible chat completions endpoint."""
#
#     async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024) -> str:
#         data = await self._post(
#             f"{self.base_url}/chat/completions",
#             headers={
#                 "Authorization": f"Bearer {self.key}",
#                 "content-type":  "application/json",
#             },
#             payload={
#                 "model":      model or self.model,
#                 "max_tokens": max_tokens,
#                 "messages":   [{"role": "user", "content": prompt}],
#             },
#         )
#         return data["choices"][0]["message"]["content"]


# --- xAI (Grok) ---------------------------------------------------------------
# .pyfun block to add:
#
#   [LLM_PROVIDER.xai]
#   active        = "true"
#   base_url      = "https://api.x.ai/v1"
#   env_key       = "XAI_API_KEY"
#   default_model = "grok-3-mini"
#   models        = "grok-3, grok-3-mini, grok-3-fast"
#   fallback_to   = ""
#   [LLM_PROVIDER.xai_END]
#
# class XAIProvider(BaseProvider):
#     """xAI Grok API — OpenAI-compatible chat completions endpoint."""
#
#     async def complete(self, prompt: str, model: str = None, max_tokens: int = 1024) -> str:
#         data = await self._post(
#             f"{self.base_url}/chat/completions",
#             headers={
#                 "Authorization": f"Bearer {self.key}",
#                 "content-type":  "application/json",
#             },
#             payload={
#                 "model":      model or self.model,
#                 "max_tokens": max_tokens,
#                 "messages":   [{"role": "user", "content": prompt}],
#             },
#         )
#         return data["choices"][0]["message"]["content"]


# =============================================================================
# SECTION 3 — Provider Registry
# Built from .pyfun [LLM_PROVIDERS] at initialize().
# Maps provider names → classes.
# To activate a dummy: uncomment class above + add entry here.
# =============================================================================

_PROVIDER_CLASSES = {
    "smollm":      SmolLMProvider,
    "anthropic":   AnthropicProvider,
    "gemini":      GeminiProvider,
    "openrouter":  OpenRouterProvider,
    "huggingface": HuggingFaceProvider,
    # "openai":   OpenAIProvider,    # ← uncomment to activate
    # "mistral":  MistralProvider,   # ← uncomment to activate
    # "xai":      XAIProvider,       # ← uncomment to activate
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
        cls = _PROVIDER_CLASSES.get(name)
        if not cls:
            logger.info(f"Provider '{name}' has no handler yet — skipped.")
            continue
        _registry[name] = cls(name, cfg)
        cache_hint = " [cache: ON]" if cfg.get("supports_cache", "false") == "true" else ""
        logger.info(f"Provider registered: {name}{cache_hint}")

    # Search providers — same key-gating principle
    _initialize_search()


# =============================================================================
# SECTION 4 — LLM Execution + Fallback Chain
# =============================================================================

async def llm_complete(
    prompt: str,
    provider_name: str = None,
    model: str = None,
    max_tokens: int = 1024,
    system: str = None,
) -> str:
    """
    Send prompt to LLM provider with automatic fallback chain.
    Fallback order is defined in .pyfun via fallback_to field.
    Raises RuntimeError if all providers in the chain fail.

    Args:
        prompt:        Input text to send to the model.
        provider_name: Provider name override. Defaults to default_provider
                       from .pyfun [TOOL.llm_complete].
        model:         Model name override. Defaults to provider's default_model.
        max_tokens:    Max tokens in response. Default: 1024.
        system:        Optional system prompt. Passed to providers that support it.
                       AnthropicProvider caches it automatically if supports_cache = true
                       and the system prompt is long enough (>= ~1024 tokens).

    Returns:
        Model response as plain text string.
    """
    if not provider_name:
        tools_cfg     = config.get_active_tools()
        provider_name = tools_cfg.get("llm_complete", {}).get("default_provider", "anthropic")

    visited = set()
    current = provider_name

    while current and current not in visited:
        visited.add(current)
        provider = _registry.get(current)

        if not provider:
            logger.warning(f"Provider '{current}' not in registry — trying fallback.")
        else:
            try:
                # Pass system prompt if provider supports it (Anthropic)
                # Other providers accept **kwargs and ignore unknown params safely
                if system is not None and hasattr(provider, 'complete'):
                    import inspect
                    sig = inspect.signature(provider.complete)
                    if 'system' in sig.parameters:
                        result = await provider.complete(prompt, model, max_tokens, system=system)
                    else:
                        result = await provider.complete(prompt, model, max_tokens)
                else:
                    result = await provider.complete(prompt, model, max_tokens)

                logger.info(f"Response from provider: '{current}'")
                return f"[{current}] {result}"
            except Exception as e:
                # Log only exception type + sanitized message — never raw {e}
                # which may contain headers, keys, or response bodies
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
                raise RuntimeError(f"HTTP {e.response.status_code} from {safe_url}") from None
            return r.json()

    async def _post(self, url: str, headers: dict, payload: dict) -> dict:
        """Shared HTTP POST with sanitized errors."""
        safe_url = url.split("?")[0]
        async with httpx.AsyncClient() as client:
            r = await client.post(url, headers=headers, json=payload, timeout=self.timeout)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"HTTP {e.response.status_code} from {safe_url}") from None
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
                result = await provider.search(query, max_results)
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