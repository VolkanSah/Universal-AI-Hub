# =============================================================================
# app/config.py
# .pyfun parser for app/* modules
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# =============================================================================
# USAGE in any app/* module:
#   from . import config
#   cfg = config.get()
#   providers = cfg["LLM_PROVIDERS"]
# =============================================================================
# USAGE
# in providers.py
# from . import config

# active = config.get_active_llm_providers()
# → { "anthropic": { "base_url": "...", "env_key": "ANTHROPIC_API_KEY", ... }, ... }
# =============================================================================
# in models.py  
# from . import config

# anthropic_models = config.get_models_for_provider("anthropic")
# =============================================================================
# in tools.py
# from . import config

# active_tools = config.get_active_tools()
# =============================================================================
import os
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger('app.config')

# Path to .pyfun — lives in app/ next to this file
PYFUN_PATH = os.path.join(os.path.dirname(__file__), ".pyfun")

# Internal cache — loaded once at first get()
_cache: Optional[Dict[str, Any]] = None


def _parse_value(value: str) -> str:
    """Strip quotes and inline comments from a value."""
    value = value.strip()
    # Remove inline comment
    if " #" in value:
        value = value[:value.index(" #")].strip()
    # Strip surrounding quotes
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return value


def _parse() -> Dict[str, Any]:
    """
    Parses the app/.pyfun file into a nested dictionary.

    Structure:
        [SECTION]
            [SUBSECTION]
                [BLOCK.name]
                key = "value"
                [BLOCK.name_END]
            [SUBSECTION_END]
        [SECTION_END]

    Returns nested dict:
        {
            "HUB": { "HUB_NAME": "...", ... },
            "LLM_PROVIDERS": {
                "anthropic": { "active": "true", "base_url": "...", ... },
                "gemini":    { ... },
            },
            "MODELS": {
                "claude-opus-4-6": { "provider": "anthropic", ... },
            },
            ...
        }
    """
    if not os.path.isfile(PYFUN_PATH):
        logger.critical(f".pyfun not found at: {PYFUN_PATH}")
        raise FileNotFoundError(f".pyfun not found at: {PYFUN_PATH}")

    result: Dict[str, Any] = {}

    # Parser state
    section: Optional[str]      = None   # e.g. "HUB", "PROVIDERS"
    subsection: Optional[str]   = None   # e.g. "LLM_PROVIDERS"
    block_type: Optional[str]   = None   # e.g. "LLM_PROVIDER", "MODEL", "TOOL"
    block_name: Optional[str]   = None   # e.g. "anthropic", "claude-opus-4-6"

    with open(PYFUN_PATH, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            # Skip empty lines and full-line comments
            if not line or line.startswith("#"):
                continue

            # Skip file identifier
            if line.startswith("[PYFUN_FILE"):
                continue

            # --- Block END markers (most specific first) ---
            if line.endswith("_END]") and "." in line:
                # e.g. [LLM_PROVIDER.anthropic_END] or [MODEL.claude-opus-4-6_END]
                block_type = None
                block_name = None
                continue

            if line.endswith("_END]") and not "." in line:
                # e.g. [LLM_PROVIDERS_END], [HUB_END], [MODELS_END]
                inner = line[1:-1].replace("_END", "")
                if subsection and inner == subsection:
                    subsection = None
                elif section and inner == section:
                    section = None
                continue

            # --- Block START markers ---
            if line.startswith("[") and line.endswith("]"):
                inner = line[1:-1]

                # Named block: [LLM_PROVIDER.anthropic] or [MODEL.claude-opus-4-6]
                if "." in inner:
                    parts = inner.split(".", 1)
                    block_type = parts[0]   # e.g. LLM_PROVIDER, MODEL, TOOL
                    block_name = parts[1]   # e.g. anthropic, claude-opus-4-6

                    # Determine which top-level key to store under
                    if block_type == "LLM_PROVIDER":
                        result.setdefault("LLM_PROVIDERS", {})
                        result["LLM_PROVIDERS"].setdefault(block_name, {})
                    elif block_type == "SEARCH_PROVIDER":
                        result.setdefault("SEARCH_PROVIDERS", {})
                        result["SEARCH_PROVIDERS"].setdefault(block_name, {})
                    elif block_type == "WEB_PROVIDER":
                        result.setdefault("WEB_PROVIDERS", {})
                        result["WEB_PROVIDERS"].setdefault(block_name, {})
                    elif block_type == "MODEL":
                        result.setdefault("MODELS", {})
                        result["MODELS"].setdefault(block_name, {})
                    elif block_type == "TOOL":
                        result.setdefault("TOOLS", {})
                        result["TOOLS"].setdefault(block_name, {})
                    continue

                # Subsection: [LLM_PROVIDERS], [SEARCH_PROVIDERS] etc.
                if section and not subsection:
                    subsection = inner
                    result.setdefault(inner, {})
                    continue

                # Top-level section: [HUB], [PROVIDERS], [MODELS] etc.
                section = inner
                result.setdefault(inner, {})
                continue

            # --- Key = Value ---
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = _parse_value(val)

                # Strip provider prefix from key (e.g. "anthropic.base_url" → "base_url")
                if block_name and key.startswith(f"{block_name}."):
                    key = key[len(block_name) + 1:]

                # Store in correct location
                if block_type and block_name:
                    if block_type == "LLM_PROVIDER":
                        result["LLM_PROVIDERS"][block_name][key] = val
                    elif block_type == "SEARCH_PROVIDER":
                        result["SEARCH_PROVIDERS"][block_name][key] = val
                    elif block_type == "WEB_PROVIDER":
                        result["WEB_PROVIDERS"][block_name][key] = val
                    elif block_type == "MODEL":
                        result["MODELS"][block_name][key] = val
                    elif block_type == "TOOL":
                        result["TOOLS"][block_name][key] = val
                elif section:
                    result[section][key] = val

    logger.info(f".pyfun loaded. Sections: {list(result.keys())}")
    return result


def load() -> Dict[str, Any]:
    """Force (re)load of .pyfun — clears cache."""
    global _cache
    _cache = _parse()
    return _cache


def get() -> Dict[str, Any]:
    """
    Returns parsed .pyfun config as nested dict.
    Loads and caches on first call — subsequent calls return cache.
    """
    global _cache
    if _cache is None:
        _cache = _parse()
    return _cache


def get_section(section: str) -> Dict[str, Any]:
    """
    Returns a specific top-level section.
    Returns empty dict if section not found.
    """
    return get().get(section, {})


def get_llm_providers() -> Dict[str, Any]:
    """Returns all LLM providers (active and inactive)."""
    return get().get("LLM_PROVIDERS", {})


def get_active_llm_providers() -> Dict[str, Any]:
    """Returns only LLM providers where active = 'true'."""
    return {
        name: cfg
        for name, cfg in get_llm_providers().items()
        if cfg.get("active", "false").lower() == "true"
    }


def get_search_providers() -> Dict[str, Any]:
    """Returns all search providers."""
    return get().get("SEARCH_PROVIDERS", {})


def get_active_search_providers() -> Dict[str, Any]:
    """Returns only search providers where active = 'true'."""
    return {
        name: cfg
        for name, cfg in get_search_providers().items()
        if cfg.get("active", "false").lower() == "true"
    }


def get_models() -> Dict[str, Any]:
    """Returns all model definitions."""
    return get().get("MODELS", {})


def get_models_for_provider(provider_name: str) -> Dict[str, Any]:
    """Returns all models for a specific provider."""
    return {
        name: cfg
        for name, cfg in get_models().items()
        if cfg.get("provider", "") == provider_name
    }


def get_tools() -> Dict[str, Any]:
    """Returns all tool definitions."""
    return get().get("TOOLS", {})


def get_active_tools() -> Dict[str, Any]:
    """Returns only tools where active = 'true'."""
    return {
        name: cfg
        for name, cfg in get_tools().items()
        if cfg.get("active", "false").lower() == "true"
    }


def get_hub() -> Dict[str, Any]:
    """Returns [HUB] section."""
    return get_section("HUB")


def get_limits() -> Dict[str, Any]:
    """Returns [HUB_LIMITS] section."""
    return get_section("HUB_LIMITS")


def get_db_sync() -> Dict[str, Any]:
    """Returns [DB_SYNC] section."""
    return get_section("DB_SYNC")


def get_debug() -> Dict[str, Any]:
    """Returns [DEBUG] section."""
    return get_section("DEBUG")


def is_debug() -> bool:
    """Returns True if DEBUG = 'ON' in .pyfun."""
    return get_debug().get("DEBUG", "OFF").upper() == "ON"


def get_pricing() -> Dict[str, Any]:
    """Returns [PRICING] section — auto model cost fetching."""
    return get_section("PRICING")


def get_discord() -> Dict[str, Any]:
    """Returns [DISCORD] section — Discord integration (inactive unless DISCORD_ACTIVE = "true")."""
    return get_section("DISCORD")


def get_gsc() -> Dict[str, Any]:
    """Returns [GSC] section — Google Search Console tool (inactive unless GSC_ACTIVE = "true")."""
    return get_section("GSC")
