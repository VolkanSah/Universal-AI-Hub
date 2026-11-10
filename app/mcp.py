# =============================================================================
# root/app/mcp.py
# 14.03.2026
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
# TRANSPORT:
#   Primary:  Streamable HTTP (MCP spec 2025-11-25) → single /mcp endpoint
#             Configured via HUB_TRANSPORT = "streamable-http" in .pyfun [HUB]
#             ASGI-App via get_asgi_app() → mounted by app/app.py
#
#   Fallback: SSE (legacy, deprecated per spec) → /mcp route via Quart
#             Configured via HUB_TRANSPORT = "sse" in .pyfun [HUB]
#             handle_request() called directly by app/app.py Quart route
#
#   All MCP traffic (both transports) passes through app/app.py first —
#   auth checks, rate limiting, logging can be added there before reaching MCP.
#
# TOOL REGISTRATION PRINCIPLE:
#   Tools are registered via tools.py — NOT hardcoded here.
#   No key = no provider = no tool = no crash.
#   Server always starts, just with fewer tools.
#   Adding a new tool = update .pyfun + providers.py only. Never touch mcp.py.
#
# DEPENDENCY CHAIN (app/* only, no fundaments!):
#   config.py    → parses app/.pyfun — single source of truth
#   providers.py → LLM + Search provider registry + fallback chain
#   models.py    → model limits, costs, capabilities from .pyfun [MODELS]
#   tools.py     → tool registry + execution — reads .pyfun [TOOLS]
#   db_sync.py   → internal SQLite IPC (app/* state) — NOT postgresql.py!
#   mcp.py       → registers tools only, delegates all logic to tools.py
# =============================================================================
import contextlib
import logging
from typing import Dict, Any
from . import config as app_config
from . import providers
from . import models
from . import pricing
from . import tools

logger = logging.getLogger('mcp')

# =============================================================================
# Globals — set once during initialize(), never touched elsewhere
# =============================================================================
_mcp       = None   # FastMCP instance
_transport = None   # "streamable-http" | "sse" — from .pyfun [HUB] HUB_TRANSPORT
_stateless = None   # True = HF Spaces / horizontal scaling safe

# =============================================================================
# Initialization — called exclusively by app/app.py
# =============================================================================
async def initialize() -> None:
    """
    Initializes the MCP instance and registers all tools.
    Called once by app/app.py during startup sequence.
    No fundaments passed in — fully sandboxed.

    Reads HUB_TRANSPORT and HUB_STATELESS from .pyfun [HUB].

    Transport modes:
        streamable-http → get_asgi_app() returns ASGI app → app.py mounts it
        sse             → handle_request() used by Quart route in app.py

    Registration order:
        1. LLM tools    → via tools.py + providers.py (key-gated)
        2. Search tools → via tools.py + providers.py (key-gated)
        3. System tools → always registered, no key required
        4. DB tools     → db_sync SELECT-only (app/* tables)
        5. Persist tools → memory orchestration (works WITHOUT PostgreSQL — queue)
    """
    global _mcp, _transport, _stateless

    hub_cfg    = app_config.get_hub()
    _transport = hub_cfg.get("HUB_TRANSPORT", "streamable-http").lower()
    _stateless = hub_cfg.get("HUB_STATELESS", "true").lower() == "true"

    logger.info(f"MCP Hub initializing (transport: {_transport}, stateless: {_stateless})...")

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        logger.critical("FastMCP not installed. Run: pip install mcp")
        raise

    _mcp = FastMCP(
        name=hub_cfg.get("HUB_NAME", "Universal MCP Hub"),
        instructions=(
            f"{hub_cfg.get('HUB_DESCRIPTION', 'Universal MCP Hub on PyFundaments')} "
            "Use list_active_tools to see what is currently available."
        ),
        stateless_http=_stateless,  # True = no session state, HF Spaces safe
    )

    # --- Initialize registries ---
    providers.initialize()
    models.initialize()
    tools.initialize()

    # --- Register MCP tools ---
    _register_llm_tools(_mcp)
    _register_search_tools(_mcp)
    _register_system_tools(_mcp)
    _register_pricing_tools(_mcp)
    _register_db_tools(_mcp)    # db_sync ready — SELECT-only, app/* tables
    _register_persist_tools(_mcp)  # memory orchestration — queue-safe, no PG required

    logger.info(f"MCP Hub initialized. Transport: {_transport}")


# =============================================================================
# ASGI App — used by app/app.py for Streamable HTTP transport
# =============================================================================
def get_asgi_app():
    """
    Returns the ASGI app for the configured transport.
    Called by app/app.py AFTER initialize() — mounted as ASGI sub-app.

    Streamable HTTP: mounts on /mcp — single endpoint for all MCP traffic.
    SSE (fallback):  returns sse_app() for legacy client compatibility.

    NOTE: For SSE transport, app/app.py uses the Quart route + handle_request()
          instead — get_asgi_app() is only called for streamable-http.
    """
    if _mcp is None:
        raise RuntimeError("MCP not initialized — call initialize() first.")

    if _transport == "streamable-http":
        logger.info("MCP ASGI app: Streamable HTTP → /mcp")
        return _mcp.streamable_http_app()
    else:
        # SSE as ASGI app — only used if app.py mounts it directly
        # (normally app.py uses the Quart route + handle_request() for SSE)
        logger.info("MCP ASGI app: SSE (legacy) → /sse")
        return _mcp.sse_app()


# =============================================================================
# Mount Prefixes — welche URL-Prefixe gehören der MCP-ASGI-App
# =============================================================================
def mount_prefixes():
    """URL prefixes the mounted ASGI app owns (used by app/app.py)."""
    if _transport == "streamable-http":
        return ("/mcp",)
    return ("/sse", "/messages")


# =============================================================================
# Lifespan — Session-Manager für die Lebensdauer des Servers
# =============================================================================
@contextlib.asynccontextmanager
async def lifespan():
    """
    Runs the FastMCP session manager for the lifetime of the server.

    WHY: FastMCP's streamable_http_app() starts its session manager in the
    Starlette lifespan. Because app/app.py mounts it behind a plain ASGI
    middleware (lifespan scope goes to Quart, not to the inner app), we have
    to run it ourselves — otherwise every request fails with
    'Task group is not initialized'.
    Call AFTER get_asgi_app() (it creates the session manager).
    """
    if _mcp is not None and _transport == "streamable-http":
        async with _mcp.session_manager.run():
            yield
    else:
        yield


# =============================================================================
# Tool Registration — delegates all logic to tools.py
# =============================================================================

def _register_llm_tools(mcp) -> None:
    """
    Register LLM completion tool.
    All logic delegated to tools.py → providers.py.
    Adding a new LLM provider = update .pyfun + providers.py. Never touch this.
    """
    if not providers.list_active_llm():
        logger.info("No active LLM providers — llm_complete tool skipped.")
        return

    @mcp.tool()
    async def llm_complete(
        prompt: str,
        provider: str = None,
        model: str = None,
        max_tokens: int = 1024,
    ) -> str:
        """
        Send a prompt to any configured LLM provider.
        Automatically follows the fallback chain defined in .pyfun if a provider fails.

        Args:
            prompt:     The input text to send to the model.
            provider:   Provider name (e.g. 'anthropic', 'gemini', 'openrouter', 'huggingface').
                        Defaults to default_provider from .pyfun [TOOL.llm_complete].
            model:      Model name override. Defaults to provider's default_model in .pyfun.
            max_tokens: Maximum tokens in the response. Default: 1024.

        Returns:
            Model response as plain text string.
        """
        return await tools.run(
            tool_name="llm_complete",
            prompt=prompt,
            provider_name=provider,
            model=model,
            max_tokens=max_tokens,
        )

    logger.info(f"Tool registered: llm_complete (active providers: {providers.list_active_llm()})")


def _register_search_tools(mcp) -> None:
    """
    Register web search tool.
    All logic delegated to tools.py → providers.py.
    Adding a new search provider = update .pyfun + providers.py. Never touch this.
    """
    if not providers.list_active_search():
        logger.info("No active search providers — web_search tool skipped.")
        return

    @mcp.tool()
    async def web_search(
        query: str,
        provider: str = None,
        max_results: int = 5,
    ) -> str:
        """
        Search the web via any configured search provider.
        Automatically follows the fallback chain defined in .pyfun if a provider fails.

        Args:
            query:       Search query string.
            provider:    Provider name (e.g. 'brave', 'tavily').
                         Defaults to default_provider from .pyfun [TOOL.web_search].
            max_results: Maximum number of results to return. Default: 5.

        Returns:
            Formatted search results as plain text string.
        """
        return await tools.run(
            tool_name="web_search",
            prompt=query,
            provider_name=provider,
            max_results=max_results,
        )

    logger.info(f"Tool registered: web_search (active providers: {providers.list_active_search()})")


def _register_system_tools(mcp) -> None:
    """
    System tools — always registered, no ENV key required.
    Exposes hub status and model info without touching secrets.
    """

    @mcp.tool()
    def list_active_tools() -> Dict[str, Any]:
        """
        List all active providers and registered tools.
        Shows ENV key names only — never exposes values or secrets.

        Returns:
            Dict with hub info, active LLM providers, active search providers,
            available tools and model names.
        """
        hub = app_config.get_hub()
        return {
            "hub":                     hub.get("HUB_NAME", "Universal MCP Hub"),
            "version":                 hub.get("HUB_VERSION", ""),
            "transport":               _transport,
            "active_llm_providers":    providers.list_active_llm(),
            "active_search_providers": providers.list_active_search(),
            "active_tools":            tools.list_all(),
            "available_models":        models.list_all(),
            "pricing_stats":           pricing.stats(),
        }

    logger.info("Tool registered: list_active_tools")

    @mcp.tool()
    def health_check() -> Dict[str, str]:
        """
        Health check endpoint for HuggingFace Spaces and monitoring systems.

        Returns:
            Dict with service status and active transport.
        """
        return {
            "status":    "ok",
            "service":   "Universal MCP Hub",
            "transport": _transport,
        }

    logger.info("Tool registered: health_check")

    @mcp.tool()
    async def get_model_info(model_name: str) -> Dict[str, Any]:
        """
        Get limits, costs, and capabilities for a specific model.
        Costs are MERGED: .pyfun manual values win, auto-fetched values
        (OpenRouter / LiteLLM) fill the gaps.

        Args:
            model_name: Model name as defined in .pyfun [MODELS] (e.g. 'claude-sonnet-4-6').

        Returns:
            Dict with context size, max output tokens, rate limits, costs, and capabilities.
            Returns pricing source info. Empty dict if model unknown.
        """
        # .pyfun [MODELS] als Basis + Auto-Pricing füllt Lücken
        merged = await pricing.get_pricing(model_name)
        if not merged.get("provider") and not merged.get("cost_input_per_1k"):
            return merged  # komplett unbekanntes Model
        return merged

    logger.info("Tool registered: get_model_info")


# =============================================================================
# Pricing Tools — auto-fetched model costs (OpenRouter + LiteLLM)
# =============================================================================
def _register_pricing_tools(mcp) -> None:
    """
    Register pricing tools.
    Answers the question nobody wants to maintain by hand:
    'Was kostet dieses Model?' — ohne [.MODELS] Kosten von Hand zu pflegen.
    """

    @mcp.tool()
    async def get_model_pricing(model_name: str) -> Dict[str, Any]:
        """
        Get current pricing for a model — auto-fetched, no manual maintenance.

        Merge order (highest priority first):
            1. .pyfun [MODELS] manual values — your override always wins
            2. OpenRouter public models API (all OR models, per-token USD)
            3. LiteLLM community price list (Anthropic, Gemini, OpenAI, ...)

        Args:
            model_name: Model name (e.g. 'claude-opus-4-6', 'gemini-2.5-flash',
                        'meta-llama/llama-3.3-70b-instruct').

        Returns:
            Dict with cost_input_per_1k, cost_output_per_1k (USD),
            context_tokens, max_output_tokens, source, pricing_note.
        """
        return await pricing.get_pricing(model_name)

    logger.info("Tool registered: get_model_pricing")

    @mcp.tool()
    async def refresh_pricing() -> Dict[str, Any]:
        """
        Force a pricing refresh from OpenRouter + LiteLLM.
        Updates the db_sync cache (TTL-gated, default 24h).

        Returns:
            Dict with fetched_at, per-source model counts, and total cached models.
        """
        return await pricing.refresh()

    logger.info("Tool registered: refresh_pricing")


# =============================================================================
# DB Tools — db_sync.py is ready, enabled
# =============================================================================
def _register_db_tools(mcp) -> None:
    """
    Register internal SQLite query tool.
    Uses db_sync.py (app/* internal SQLite) — NOT postgresql.py (Guardian-only)!

    SECURITY: Only SELECT queries are permitted.
    Enforced at application level in db_sync.query() — not just in docs.
    Tables accessible: hub_state, tool_cache  (app/* only)
    Tables blocked:    users, sessions         (Guardian-only, different owner)

    Requirements (all met):
        1. This function enabled
        2. _register_db_tools(_mcp) called in initialize()
        3. db_sync.initialize() runs in app/app.py BEFORE mcp.initialize()
    """
    from . import db_sync

    @mcp.tool()
    async def db_query(sql: str) -> list:
        """
        Execute a read-only SELECT query on the internal hub state database.

        Only SELECT statements are permitted — all write operations are blocked
        at the db_sync layer (not just by convention).

        Accessible tables:
            hub_state   — current hub runtime state (tool status, uptime, etc.)
            tool_cache  — cached tool results for repeated queries

        NOT accessible (Guardian-only):
            users       — managed by fundaments/user_handler.py
            sessions    — managed by fundaments/user_handler.py

        Args:
            sql: SQL SELECT statement. Example: "SELECT * FROM hub_state LIMIT 10"

        Returns:
            List of result rows as dicts. Empty list if no results.

        Raises:
            ValueError: If statement is not a SELECT query.
            RuntimeError: If db_sync is not initialized.
        """
        return await db_sync.query(sql)

    logger.info("Tool registered: db_query (SQLite SELECT-only, app/* tables)")


# =============================================================================
# Persist Tools — Gedächtnis-Orchestrierung (SQLite-Queue → PostgreSQL)
# =============================================================================
def _register_persist_tools(mcp) -> None:
    """
    Register memory-orchestration tools.

    persist_result works WITHOUT PostgreSQL: entries queue restart-safe in
    the SQLite persist_queue (db_sync) and flush automatically once the
    Guardian-injected PostgreSQL bridge is active. No key = queued, not crash.
    """
    if "persist_result" not in tools.list_all():
        logger.info("persist_result not active in .pyfun — skipped.")
        return

    @mcp.tool()
    async def persist_result(
        prompt: str = None,
        source: str = None,
        model: str = None,
        score: float = None,
    ) -> Dict[str, Any]:
        """
        Persist a result to long-term memory (PostgreSQL).

        Restart-safe: without DATABASE_URL the entry queues in SQLite and
        flushes automatically once PostgreSQL is available. Never crashes.

        Args:
            prompt: The text/result to persist (or hub_state key via .pyfun state_read_key).
            source: Which tool/component produced this entry (optional).
            model:  Which model produced this entry (optional).
            score:  Optional quality score — reserved slot, set by your own filter.

        Returns:
            Dict with status ('persisted' | 'queued'), target_table, queue_depth.
        """
        return await tools.run(
            tool_name="persist_result",
            prompt=prompt,
            source=source,
            model=model,
            score=score,
        )

    logger.info("Tool registered: persist_result (queue-safe memory orchestration)")

    @mcp.tool()
    async def persist_status() -> Dict[str, Any]:
        """
        Show memory orchestration status: queue depth, oldest entry,
        failed attempts, and whether the PostgreSQL bridge is active.

        Returns:
            Dict with queue stats — observability for the memory pipeline.
        """
        from . import db_sync
        return await db_sync.persist_queue_stats()

    logger.info("Tool registered: persist_status")


# =============================================================================
# Direct execution guard
# =============================================================================
if __name__ == '__main__':
    print("WARNING: Run via main.py → app.py, not directly.")
