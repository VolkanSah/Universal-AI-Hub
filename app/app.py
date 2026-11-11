# =============================================================================
# root/app/app.py
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# Repo: https://github.com/VolkanSah/Universal-MCP-Hub-sandboxed
# =============================================================================
# ARCHITECTURE NOTE:
#   This file is the Orchestrator of the sandboxed app/* layer.
#   It is ONLY started by main.py (the "Guardian").
#   All fundament services are injected via the `fundaments` dictionary.
#   Direct execution is blocked by design.
#
# SANDBOX RULES:
#   - fundaments dict is ONLY unpacked inside start_application()
#   - fundaments are NEVER stored globally or passed to other app/* modules
#   - app/* modules read their own config from app/.pyfun
#   - app/* internal state/IPC uses app/db_sync.py (SQLite) — NOT postgresql.py
#   - Secrets stay in .env → Guardian reads them → never touched by app/*
# =============================================================================
from quart import Quart, request, jsonify  # async Flask — ASGI compatible
import logging
from hypercorn.asyncio import serve        # ASGI server — async native, replaces waitress
from hypercorn.config import Config        # hypercorn config
from datetime import datetime, timezone
import asyncio
from typing import Dict, Any
# =============================================================================
# Import app/* modules — MINIMAL BUILD
# Each module reads its own config from app/.pyfun independently.
# NO fundaments passed into these modules!
# =============================================================================
from . import mcp                   # MCP transport layer (Streamable HTTP / SSE via ASGI mount)
from . import config as app_config  # app/.pyfun parser — used only in app/*
from . import providers    # API provider registry — reads app/.pyfun
from . import models       # Model config + token/rate limits — reads app/.pyfun
from . import pricing      # Auto model costs (OpenRouter + LiteLLM) — reads app/.pyfun
from . import tools        # MCP tool definitions + provider mapping — reads app/.pyfun
from . import db_sync      # Internal SQLite IPC — app/* state & communication
from . import shellmaster  # ShellMaster 2.0 — safe shell assistant (inactive by default)
from . import auth         # Optional shared-token gate for /api, /crypto, /mcp (off without token)
#                          # db_sync ≠ postgresql.py! Cloud DB is Guardian-only.
# Future modules (will uncomment when ready):
# from . import discord_api  # Discord bot integration
# from . import hf_hooks     # HuggingFace Space hooks
# from . import git_hooks    # GitHub/GitLab webhook handler
# from . import web_api      # Generic REST API handler
# =============================================================================
# Loggers — one per module for clean log filtering
# =============================================================================
logger        = logging.getLogger('application')
logger_mcp    = logging.getLogger('mcp')
logger_config = logging.getLogger('config')
logger_tools     = logging.getLogger('tools')
logger_providers = logging.getLogger('providers')
logger_models    = logging.getLogger('models')
logger_db_sync   = logging.getLogger('db_sync')



# ── NEU: nach den Imports, vor app = Quart(__name__) ──────────────────────────

def _make_mount_middleware(outer_app, prefixes, inner_app, strip_prefix: bool = False):
    """
    ASGI-Middleware: leitet Requests mit passendem Prefix an inner_app
    (FastMCP Streamable HTTP / SSE) weiter, alles andere geht an outer_app (Quart).
    Lifespan-Scopes gehen immer an outer_app (Quart).

    WICHTIG: Prefix wird standardmaessig NICHT gestript - FastMCP registriert
    seine Route intern selbst (settings.streamable_http_path = "/mcp").
    Wer stript, erzeugt 404s. Genau das war der Break im alten Build.
    """
    async def middleware(scope, receive, send):
        path = scope.get("path", "")
        for prefix in prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                if strip_prefix:
                    scope = dict(scope)
                    stripped = path[len(prefix):] or "/"
                    scope["path"] = stripped
                    scope["raw_path"] = stripped.encode()
                await inner_app(scope, receive, send)
                return
        await outer_app(scope, receive, send)
    return middleware

# =============================================================================
# Quart app instance
# =============================================================================
app = Quart(__name__)
START_TIME = datetime.now(timezone.utc)
# =============================================================================
# Quart Routes
# =============================================================================
@app.route("/", methods=["GET"])
async def health_check():
    """
    Health check endpoint.
    Used by HuggingFace Spaces and monitoring systems to verify the app is running.
    """
    uptime = datetime.now(timezone.utc) - START_TIME
    return jsonify({
        "status": "running",
        "service": "Universal MCP Hub",
        "uptime_seconds": int(uptime.total_seconds()),
    })


@app.route("/api", methods=["POST"])
async def api_endpoint():
    """
    REST tool gateway: POST {"tool": "name", "params": {...}}
    Auth: optional shared token — see app/auth.py (.pyfun [HUB] HUB_AUTH_ENV).
    """
    try:
        data      = await request.get_json(silent=True) or {}
        tool_name = data.get("tool")
        params    = data.get("params", {}) or {}

        if not tool_name:
            return jsonify({"error": "Missing 'tool'"}), 400
        if not isinstance(params, dict):
            return jsonify({"error": "'params' must be an object"}), 400
        params.pop("tool_name", None)  # would collide with tools.run(tool_name, ...) → 500

        # System tools — handle directly, no prompt needed!
        if tool_name == "list_active_tools":
            return jsonify({"result": {
                "active_tools":            tools.list_all(),
                "active_llm_providers":    providers.list_active_llm(),
                "active_search_providers": providers.list_active_search(),
                "available_models":        models.list_all(),
                "pricing_stats":           pricing.stats(),
            }})

        if tool_name == "health_check":
            return jsonify({"result": {"status": "ok"}})

        # db_query — handled by db_sync directly, not tools.run()
        if tool_name == "db_query":
            sql = params.get("sql", "") or params.get("prompt", "")
            if not sql:
                return jsonify({"error": "db_query requires 'sql' (or 'prompt') parameter"}), 400
            result = await db_sync.query(sql)
            return jsonify({"result": result})

        # persist_status — memory orchestration observability (no prompt needed)
        if tool_name == "persist_status":
            return jsonify({"result": await db_sync.persist_queue_stats()})

        # usage_stats — tokens + estimated cost per model/provider/tool/day
        if tool_name == "usage_stats":
            result = await db_sync.usage_stats(
                group_by=str(params.get("group_by", "model")),
                days=int(params.get("days") or 0) or None,
            )
            return jsonify({"result": result})

        # get_model_pricing — auto-fetched costs, .pyfun values win as override
        if tool_name == "get_model_pricing":
            model_name = params.get("model_name", "") or params.get("model", "")
            if not model_name:
                return jsonify({"error": "get_model_pricing requires 'model_name'"}), 400
            result = await pricing.get_pricing(model_name)
            return jsonify({"result": result})

        # rename 'provider' → 'provider_name' for tools.run()
        if "provider" in params:
            params["provider_name"] = params.pop("provider")

        result = await tools.run(tool_name, **params)
        return jsonify({"result": result})

    except ValueError as e:                    # unknown tool / missing prompt → 400
        return jsonify({"error": str(e)}), 400
    except KeyError as e:
        return jsonify({"error": str(e.args[0])}), 404
    except RuntimeError as e:
        # Provider-Kette erschöpft — Message ist upstream sanitisiert
        # (nur HTTP-Status + safe_url, niemals Keys/Bodies)
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        logger.error(f"API error: {type(e).__name__}: {e}")
        return jsonify({"error": "Internal error"}), 500


@app.route("/crypto", methods=["POST"])
async def crypto_endpoint():
    """
    Encrypted API endpoint.
    Encryption handled by app/* layer — no direct fundaments access here.
    """
    # TODO: implement via app/* encryption wrapper
    data = await request.get_json()
    return jsonify({"status": "not_implemented"}), 501


# Future routes (uncomment when ready):
# @app.route("/discord", methods=["POST"])
# async def discord_interactions():
#     """Discord interactions endpoint — signature verification via discord_api module."""
#     pass

# @app.route("/webhook/hf", methods=["POST"])
# async def hf_webhook():
#     """HuggingFace Space event hooks."""
#     pass

# @app.route("/webhook/git", methods=["POST"])
# async def git_webhook():
#     """GitHub / GitLab webhook handler."""
#     pass
# =============================================================================
# Main entry point — called exclusively by Guardian (main.py)
# =============================================================================
async def start_application(fundaments: Dict[str, Any]) -> None:
    """
    Main entry point for the sandboxed app layer.
    Called exclusively by main.py after all fundament services are initialized.

    Args:
        fundaments: Dictionary of initialized services from Guardian (main.py).
                    Services are unpacked here and NEVER stored globally or
                    passed into other app/* modules.
    """
    logger.info("Application starting...")

    # =========================================================================
    # Unpack fundaments — ONLY here, NEVER elsewhere in app/*
    # These are the 6 fundament services from fundaments/*
    # =========================================================================
    config_service          = fundaments["config"]          # fundaments/config_handler.py
    db_service              = fundaments["db"]              # fundaments/postgresql.py — None if not configured
    encryption_service      = fundaments["encryption"]      # fundaments/encryption.py — None if keys not set
    access_control_service  = fundaments["access_control"]  # fundaments/access_control.py — None if no DB
    user_handler_service    = fundaments["user_handler"]    # fundaments/user_handler.py — None if no DB
    security_service        = fundaments["security"]        # fundaments/security.py — None if deps missing

    # --- Log active fundament services ---
    if encryption_service:
        logger.info("Encryption service active.")

    if user_handler_service and security_service:
        logger.info("Auth services active (user_handler + security).")

    if access_control_service and security_service:
        logger.info("Access control active.")

    if db_service and not user_handler_service:
        logger.info("Database-only mode active (e.g. ML pipeline).")

    if not db_service:
        logger.info("Database-free mode active (e.g. Discord bot, API client).")

    # =========================================================================
    # Initialize app/* internal services — MINIMAL BUILD
    # Each module reads its own config from app/.pyfun — no fundaments passed in.
    # =========================================================================

    # db_sync only if cloud_DB used to!
    # PSQL bridge — nur wenn Guardian DB-Service injiziert hat
    await db_sync.initialize()

    if db_service:
        # asyncpg Pool direkt nutzen — kein execute_secured_query nötig
        db_sync.set_psql_writer(db_service.execute)
        logger.info("PostgreSQL bridge active.")
    else:
        logger.info("PostgreSQL bridge inactive — no DATABASE_URL configured.")

    # Memory-Orchestrierung: Retry-Flusher starten — läuft MIT und OHNE PG.
    # Ohne Writer bleibt alles restart-safe in der SQLite-Queue liegen
    # (persist_result ohne DATABASE_URL = queued, kein Crash).
    db_sync.start_persist_flusher()

    # ShellMaster — bleibt inaktiv, bis .pyfun [TOOL.shellmaster] active="true"
    # UND token + commands-Datei vorhanden sind (siehe shellmaster.initialize)
    await shellmaster.initialize()

    # Auto-Pricing — füllt Kosten aus OpenRouter/LiteLLM nach, .pyfun gewinnt.
    # Läuft im Hintergrund — blockiert den Start NICHT (no fetch = no crash).
    await pricing.initialize()

    await mcp.initialize()

    # =========================================================================
    # MCP: ASGI-App holen, DANACH mounten, DANACH lifespan starten
    # (get_asgi_app() muss VOR mcp.lifespan() kommen — siehe mcp.py)
    # =========================================================================
    hub_cfg   = app_config.get_hub()
    mcp_asgi  = mcp.get_asgi_app()
    app.asgi_app = _make_mount_middleware(app.asgi_app, mcp.mount_prefixes(), mcp_asgi)
    logger.info(f"MCP mounted: {', '.join(mcp.mount_prefixes())}")

    # Optional token gate (outermost layer). No token configured = open, as before.
    app.asgi_app = auth.make_middleware(
        app.asgi_app,
        auth.token_from_env(hub_cfg.get("HUB_AUTH_ENV", "")),
        ("/api", "/crypto") + tuple(mcp.mount_prefixes()),
    )

    # --- Read HOST/PORT from app/.pyfun [HUB] ---
    host = hub_cfg.get("HUB_HOST", "0.0.0.0")
    port = int(hub_cfg.get("HUB_PORT", "7860"))  # hub_cfg bereits gelesen

    # --- Configure hypercorn ---
    config = Config()
    config.bind = [f"{host}:{port}"]

    logger.info(f"Starting hypercorn on {host}:{port}...")

    # --- Run hypercorn — blocks until shutdown ---
    # mcp.lifespan() ist PFLICHT: startet den FastMCP Session-Manager.
    # Ohne ihn schlägt jeder /mcp-Request mit
    # 'Task group is not initialized' fehl.
    async with mcp.lifespan():
        logger.info("All services running.")
        await serve(app, config)

# =============================================================================
# Direct execution guard
# =============================================================================
if __name__ == '__main__':
    print("WARNING: Running app.py directly. Fundament modules might not be correctly initialized.")
    print("Please run 'python main.py' instead for proper initialization.")

    test_fundaments = {
        "config":           None,
        "db":               None,
        "encryption":       None,
        "access_control":   None,
        "user_handler":     None,
        "security":         None,
    }

    asyncio.run(start_application(test_fundaments))