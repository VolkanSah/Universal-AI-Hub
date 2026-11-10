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

    # prompt ist für llm/search/db Pflicht — sauberer 400er statt TypeError/500er
    # persist ist die Ausnahme: ohne state_read_key braucht es einen prompt,
    # mit state_read_key liest es stattdessen aus hub_state (Gedächtnis-Pattern).
    if provider_type in ("llm", "search", "db") and (prompt is None or str(prompt).strip() == ""):
        raise ValueError(
            f"Tool '{tool_name}' requires a 'prompt' parameter (for db tools: the SQL query)."
        )
    if provider_type == "persist" and not tool_cfg.get("state_read_key", "") \
            and (prompt is None or str(prompt).strip() == ""):
        raise ValueError(
            "persist_result requires 'prompt' (or set state_read_key in .pyfun [TOOL.persist_result])."
        )

    # Build full prompt — prepend system_prompt if defined in .pyfun
    full_prompt = f"{system_prompt}\n\n{prompt}".strip() if system_prompt else prompt

    # --- LLM tools ---
    if provider_type == "llm":
        return await providers.llm_complete(
            prompt=full_prompt,
            provider_name=default_provider,
            model=model,
            max_tokens=max_tokens,
        )

    # --- Search tools ---
    if provider_type == "search":
        return await providers.search(
            query=prompt,
            provider_name=default_provider,
            max_results=max_results or int(tool_cfg.get("default_results", "5")),
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
        flush = await db_sync.flush_persist_queue()  # best effort — sofortiger Versuch
        if flush.get("flushed", 0) > 0:
            status, note = "persisted", "written to PostgreSQL"
        else:
            status, note = "queued", "restart-safe in SQLite — flushes to PostgreSQL when the bridge becomes active"
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
