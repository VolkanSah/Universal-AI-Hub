# =============================================================================
# # app/models.py
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
#
# TOOL REGISTRATION PRINCIPLE:
#   Tools are registered via models.py and models.py .
#   No key = no provider = no tool = no crash.
#   Adding a new provider = update .pyfun + providers.py only. Never touch mcp.py!
#
# DEPENDENCY CHAIN (app/* only, no fundaments!):
#   config.py    → parses app/.pyfun — single source of truth
#   providers.py → LLM + Search provider registry + fallback chain
#   models.py    → model limits, costs, capabilities from .pyfun [MODELS]
#   db_sync.py   → internal SQLite IPC (app/* state) — NOT postgresql.py!
#   mcp.py       → registers tools only, delegates all logic to providers/*
# =============================================================================
# app/models.py
from . import config
import logging

logger = logging.getLogger("models")

_registry: dict = {}

def initialize() -> None:
    """Build model registry from .pyfun [MODELS]"""
    global _registry
    _registry = config.get_models()
    logger.info(f"Models loaded: {list(_registry.keys())}")


def get(model_name: str) -> dict:
    """Get model config by name. Falls back to raw .pyfun if not initialized yet."""
    if _registry:
        return _registry.get(model_name, {})
    return config.get_models().get(model_name, {})


def get_limit(model_name: str, key: str, default=None):
    """Get specific limit for a model."""
    return _registry.get(model_name, {}).get(key, default)


def for_provider(provider_name: str) -> dict:
    """Get all models for a provider."""
    return config.get_models_for_provider(provider_name)


def max_tokens(model_name: str) -> int:
    return int(get_limit(model_name, "max_output_tokens", "1024"))


def context_size(model_name: str) -> int:
    return int(get_limit(model_name, "context_tokens", "4096"))


def cost_input(model_name: str) -> float:
    return float(get_limit(model_name, "cost_input_per_1k", "0"))


def cost_output(model_name: str) -> float:
    return float(get_limit(model_name, "cost_output_per_1k", "0"))


def list_all() -> list:
    return list(_registry.keys())
