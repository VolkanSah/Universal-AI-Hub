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
    prompt: str,
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    max_results: int = 5,
) -> str:
    """
    Execute a tool by name.
    Reads tool config from registry, delegates to providers.py.
    Applies system_prompt from .pyfun if defined.

    Args:
        tool_name:     Tool name as defined in .pyfun [TOOLS] (e.g. 'llm_complete').
        prompt:        User input / query string.
        provider_name: Override provider. Defaults to tool's default_provider in .pyfun.
        model:         Override model. Defaults to provider's default_model in .pyfun.
        max_tokens:    Max tokens for LLM response. Default: 1024.

    Returns:
        Tool response as plain text string.

    Raises:
        ValueError:   If tool_name is not found in registry.
        RuntimeError: If all providers fail (propagated from providers.py).
    """
    tool_cfg = _registry.get(tool_name)
    if not tool_cfg:
        raise ValueError(f"Tool '{tool_name}' not found in registry or not active.")

    provider_type    = tool_cfg.get("provider_type", "llm")
    default_provider = provider_name or tool_cfg.get("default_provider", "")
    system_prompt    = tool_cfg.get("system_prompt", "")

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
            max_results=int(tool_cfg.get("default_results", "5")),
        )

    # --- DB tools (read-only, delegated to db_sync when ready) ---
    if provider_type == "db":
        sql = prompt  # prompt ist hier die SQL-Query
        return await db_sync.query(sql)

    # in run() — neuer persist-Block:
    if provider_type == "persist":
    # hub_state lesen + an PSQL schicken via db_sync bridge
        key   = tool_cfg.get("state_read_key", "")
        table = tool_cfg.get("target_table", "hub_results")
        data  = await db_sync.read(key) if key else {"prompt": prompt}
        await db_sync.persist(table, data)
        return f"Persisted to PostgreSQL table '{table}'."

    # --- Unknown provider type ---
    logger.warning(f"Tool '{tool_name}' has unknown provider_type '{provider_type}' — skipped.")
    return f"Tool '{tool_name}' provider type '{provider_type}' is not yet implemented."
    # In run() — add after the existing "persist" block:
 
    # --- Heal tools (self-healing runtime debugger) --------------------------
    if provider_type == "heal":
        from . import healer
        from . import providers as _providers
 
        async def _llm(prompt, system):
            return await _providers.llm_complete(
                prompt=prompt,
                provider_name=default_provider or None,
                system=system,
            )
 
        result = await healer.heal(code=prompt, llm_fn=_llm)
        return result.to_text()
 
    if provider_type == "heal_generate":
        from . import healer
        from . import providers as _providers
 
        async def _llm(prompt, system):
            return await _providers.llm_complete(
                prompt=prompt,
                provider_name=default_provider or None,
                system=system,
            )
 
        result = await healer.generate_and_heal(task=prompt, llm_fn=_llm)
        return result.to_text()
 
 
# =============================================================================



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
