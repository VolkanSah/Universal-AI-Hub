# extractet from our discord community bank :D
# =============================================================================
# app/discord_api.py
# Discord Integration — Modular API layer (REST + Interactions Endpoint)
# Universal My AI HUB (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# This file is under AGPL License V. 3 + ESOL 2.1
# Repo: https://github.com/VolkanSah/Multi-LLM-API-Gateway
# =============================================================================
# ARCHITECTURE NOTE:
#   Modular integration living in app/* only.
#   NO low-level access, NO fundaments access. Config comes from
#   app/.pyfun [DISCORD] via app/config.py — secrets stay in .env
#   (env_key = NAME only, same rule as every provider).
#
#   ZERO new dependencies — deliberately:
#     httpx        → REST calls (already required by the hub)
#     cryptography → Ed25519 signature verify (PyFundaments core!)
#   The discord.py library + PyNaCl are NOT needed. Pure HTTP.
#   (Only if you later want gateway events or voice: uncomment in
#   requirements.txt — this module never imports them.)
#
# WHY "discord_api.py" AND NOT "discord.py":
#   A file named discord.py that imports the discord library is the
#   classic self-import footgun (works in the package, explodes the
#   moment anything runs inside app/). app.py already references this
#   module as discord_api (line 43 + /discord route stub).
#
# THREE CAPABILITIES — use what you need, ignore the rest:
#
#   1. OUTBOUND (hub → Discord):
#      send_message()   — bot posts to a channel (REST, Bearer token)
#      send_webhook()   — write-only channel webhook (no bot needed)
#      read_channel()   — last N messages (needs Message Content Intent)
#      send_followup()  — late answer after defer (LLM latency pattern)
#
#   2. INBOUND (Discord → hub) — Interactions Endpoint:
#      verify_signature()    — Ed25519 check, rejects forgeries (401)
#      handle_interaction()  — PING/PONG + slash command dispatch
#      register_command_handler() — later: map commands to hub tools
#
#   3. SETUP HELPERS:
#      register_commands()  — register slash commands via REST
#      get_me()             — token sanity check (GET /users/@me)
#
# FLOW (interactions, the >3s LLM pattern):
#   Discord POSTs a command → verify_signature (raw bytes, BEFORE parse!)
#   → respond {"type": 5} (DEFERRED) → run the LLM/tool in background
#   → send_followup(interaction_token, answer)
#   Discord kills responses after 3 seconds — defer is not optional
#   for LLM latency. 3s is a hard Discord limit, not ours.
#
# STATUS: shipped INACTIVE (DISCORD_ACTIVE = "false" in .pyfun).
#   Wiring comes later — prepared hooks:
#     app/app.py:  from . import discord_api + /discord route (stub exists)
#     app/tools.py: elif provider_type == "discord": ...
#   See docs/discord_api.md for the exact snippets.
# =============================================================================

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Optional

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import config

logger = logging.getLogger("discord_api")

# Discord interaction response types (only the ones we use)
RESPONSE_PONG                        = 1   # answer to PING (validation)
RESPONSE_CHANNEL_MESSAGE             = 4   # direct answer
RESPONSE_DEFERRED_CHANNEL_MESSAGE    = 5   # "thinking..." — followup later

# Interaction request types (from Discord)
TYPE_PING               = 1
TYPE_APPLICATION_COMMAND = 2

# Signature freshness — reject stale timestamps (replay protection).
# Discord signs timestamp + body; anything older than this is garbage.
_TIMESTAMP_MAX_AGE = 300  # seconds

# Content limit enforced by Discord — we reject instead of silently
# truncating. The caller (later: tools.py wrapper) decides how to split.
_MAX_CONTENT_LEN = 2000


# =============================================================================
# Module State — populated by initialize()
# =============================================================================
_cfg          = {}
_active       = False
_token        = ""          # from .env via env_key — NEVER in .pyfun
_webhook_url  = ""          # from .env via webhook_env_key — NEVER in .pyfun
_default_channel = ""
_app_id       = ""          # Dev Portal → Application ID (public)
_public_key   = ""          # Dev Portal → Public Key (public — verify only!)
_api_base     = "https://discord.com/api/v10"
_timeout      = 15

# Slash command handlers — name → async fn(interaction) → response dict.
# Populated later by tools.py wiring; ships with the built-in "ping".
_command_handlers: Dict[str, Callable] = {}


# =============================================================================
# Initialization — called by app/app.py (later, when wired)
# =============================================================================

async def initialize() -> None:
    """
    Initialize Discord integration from app/.pyfun [DISCORD].
    Called by app/app.py — never reads os.environ beyond the named env_key
    (same sandbox boundary as shellmaster / every provider).

    No token + no webhook + no public key → module stays inactive.
    No crash, no error — just a log line. Hub philosophy.
    """
    global _cfg, _active, _token, _webhook_url, _default_channel
    global _app_id, _public_key, _api_base, _timeout

    _cfg = config.get_discord()

    if not _cfg or _cfg.get("DISCORD_ACTIVE", "false").lower() != "true":
        logger.info("Discord not active in .pyfun [DISCORD] — skipped")
        return

    # Token + Webhook: ENV zuerst (.pyfun nennt nur den ENV-Namen).
    # Secrets gehören NIE in .pyfun — gleiche Regel wie bei allen Providern.
    env_key        = _cfg.get("DISCORD_ENV_KEY", "DISCORD_BOT_TOKEN")
    webhook_env    = _cfg.get("DISCORD_WEBHOOK_ENV_KEY", "DISCORD_WEBHOOK_URL")
    _token         = os.getenv(env_key, "") if env_key else ""
    _webhook_url   = os.getenv(webhook_env, "") if webhook_env else ""

    # Public values from .pyfun (safe — they are not secrets):
    _default_channel = _cfg.get("DISCORD_DEFAULT_CHANNEL", "")
    _app_id          = _cfg.get("DISCORD_APP_ID", "")
    _public_key      = _cfg.get("DISCORD_APP_PUBLIC_KEY", "")
    _api_base        = _cfg.get("DISCORD_API_BASE", "https://discord.com/api/v10")
    _timeout         = int(_cfg.get("DISCORD_TIMEOUT_SEC", "15"))

    if not _token and not _webhook_url:
        logger.warning(
            "Discord: neither %s nor %s set in .env — outbound disabled "
            "(set one of them, module stays importable)", env_key, webhook_env
        )
    if _app_id and not _token:
        logger.warning("Discord: DISCORD_APP_ID set but no bot token — followup/commands disabled")

    _active = True
    modes = []
    if _token:            modes.append("bot-rest")
    if _webhook_url:      modes.append("webhook")
    if _public_key:       modes.append("interactions-verify")
    logger.info("Discord initialized | modes: %s", ", ".join(modes) or "none (config only)")


def is_ready() -> bool:
    """True if initialize() ran with DISCORD_ACTIVE = "true"."""
    return _active


def get_config() -> dict:
    """Raw [DISCORD] config from .pyfun."""
    return _cfg


def get_status() -> dict:
    """
    Safe status snapshot — key NAMES and booleans only, never values.
    (Same principle as list_active_tools: nothing secret leaves the module.)
    """
    return {
        "active":          _active,
        "bot_token_set":   bool(_token),
        "webhook_set":     bool(_webhook_url),
        "public_key_set":  bool(_public_key),
        "app_id_set":      bool(_app_id),
        "default_channel": _default_channel,
        "api_base":        _api_base,
        "commands":        sorted(_command_handlers.keys()),
    }


# =============================================================================
# Internal REST helper — Bearer auth, timeout, one rate-limit retry
# =============================================================================

async def _api_request(
    method: str,
    path: str,
    payload: Optional[dict] = None,
    auth: bool = True,
) -> dict:
    """
    One Discord REST call with the house rules:
    - Bearer token only when auth=True (webhook/followup URLs carry their own)
    - 429 → sleep Retry-After (capped at 10s), retry ONCE, then give up
    - never raises for flow errors — returns {"ok": False, "error": ...}
    """
    if auth and not _token:
        return {"ok": False, "error": "no bot token — set DISCORD_BOT_TOKEN in .env"}

    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {_token}"

    url = path if path.startswith("http") else f"{_api_base}{path}"

    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.request(
                    method, url, json=payload, headers=headers, timeout=_timeout,
                )

            if r.status_code == 429 and attempt == 1:
                retry_after = float(r.headers.get("Retry-After", "1"))
                retry_after = min(retry_after, 10.0)
                logger.warning("Discord rate limit — retrying in %.1fs", retry_after)
                await asyncio.sleep(retry_after)
                continue

            if 200 <= r.status_code < 300:
                data = r.json() if r.content else {}
                return {"ok": True, "status": r.status_code, "data": data}

            # Discord error body: {"message": ..., "code": ...} — keep it, drop secrets
            try:
                err = r.json().get("message", "")
            except Exception:
                err = ""
            logger.warning("Discord API error HTTP %s: %s", r.status_code, err[:120])
            return {"ok": False, "status": r.status_code, "error": err or f"HTTP {r.status_code}"}

        except httpx.ConnectError:
            return {"ok": False, "error": "connection failed — Discord unreachable?"}
        except httpx.TimeoutException:
            return {"ok": False, "error": f"timeout after {_timeout}s"}
        except Exception as e:
            # Sanitized — only the exception type, no payload details
            logger.warning("Discord request failed: %s", type(e).__name__)
            return {"ok": False, "error": type(e).__name__}

    return {"ok": False, "error": "rate limited twice — try later"}


# =============================================================================
# OUTBOUND — hub → Discord
# =============================================================================

async def get_me() -> dict:
    """
    Token sanity check — GET /users/@me.
    Returns the bot's own user object (id, username, ...) on success.
    Best first live test after wiring the token.
    """
    return await _api_request("GET", "/users/@me")


async def send_message(
    channel_id: str,
    content: str,
    embed: Optional[dict] = None,
) -> dict:
    """
    Post a message to a channel via Bot REST.

    Args:
        channel_id: target channel (falls back to DISCORD_DEFAULT_CHANNEL).
        content:    message text. Discord hard limit: 2000 chars — we REJECT
                    longer input instead of silently truncating.
        embed:      raw Discord embed JSON dict (pass-through, no builder).

    Returns:
        {"ok": True, "status": 200, "data": <message object>} on success,
        {"ok": False, "error": ...} on failure. Never raises.
    """
    if not _active:
        return {"ok": False, "error": "Discord not active — set DISCORD_ACTIVE = \"true\" in .pyfun"}
    if not _token:
        return {"ok": False, "error": "no bot token — set DISCORD_BOT_TOKEN in .env"}

    channel_id = channel_id or _default_channel
    if not channel_id:
        return {"ok": False, "error": "no channel_id given and DISCORD_DEFAULT_CHANNEL empty"}
    if content and len(content) > _MAX_CONTENT_LEN:
        return {"ok": False, "error": f"content exceeds {_MAX_CONTENT_LEN} chars (Discord limit)"}
    if not content and not embed:
        return {"ok": False, "error": "content and embed are both empty"}

    payload = {"content": content}
    if embed:
        payload["embeds"] = [embed]

    result = await _api_request("POST", f"/channels/{channel_id}/messages", payload)
    if result.get("ok"):
        logger.info("Discord: message sent to channel %s", channel_id)
    return result


async def send_webhook(content: str = "", embed: Optional[dict] = None) -> dict:
    """
    Post via channel webhook — write-only, needs NO bot, NO invite,
    NO intents. Dead simple notifications. The URL is the secret
    (anyone holding it can post) → lives in .env only.

    Args:
        content: message text (same 2000 char limit, rejected not truncated).
        embed:   raw Discord embed JSON dict (pass-through).
    """
    if not _active:
        return {"ok": False, "error": "Discord not active — set DISCORD_ACTIVE = \"true\" in .pyfun"}
    if not _webhook_url:
        return {"ok": False, "error": "no webhook — set DISCORD_WEBHOOK_URL in .env"}
    if content and len(content) > _MAX_CONTENT_LEN:
        return {"ok": False, "error": f"content exceeds {_MAX_CONTENT_LEN} chars (Discord limit)"}
    if not content and not embed:
        return {"ok": False, "error": "content and embed are both empty"}

    payload: Dict[str, Any] = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]

    # Webhook URL carries its own auth — no Bearer header
    result = await _api_request("POST", _webhook_url, payload, auth=False)
    if result.get("ok"):
        logger.info("Discord: webhook message sent")
    return result


async def read_channel(channel_id: str, limit: int = 10) -> dict:
    """
    Read the last N messages from a channel (newest first).

    NOTE: message TEXT only comes back if the bot has the
    "Message Content Intent" enabled in the Discord Developer Portal
    (Bot tab). Without it you get metadata, content comes back empty.
    Send-only setups don't need the intent.
    """
    if not _active:
        return {"ok": False, "error": "Discord not active — set DISCORD_ACTIVE = \"true\" in .pyfun"}
    if not _token:
        return {"ok": False, "error": "no bot token — set DISCORD_BOT_TOKEN in .env"}

    channel_id = channel_id or _default_channel
    if not channel_id:
        return {"ok": False, "error": "no channel_id given and DISCORD_DEFAULT_CHANNEL empty"}

    limit = max(1, min(int(limit), 50))  # Discord caps at 100, we cap tighter
    return await _api_request("GET", f"/channels/{channel_id}/messages?limit={limit}")


async def send_followup(interaction_token: str, content: str, embed: Optional[dict] = None) -> dict:
    """
    Late answer after a DEFERRED response (type 5).

    The interaction token authorizes the call itself (webhook-style) —
    no bot token needed. Valid for 15 minutes after the interaction.

    Args:
        interaction_token: token from the interaction payload.
        content:           the (possibly slow) LLM answer.
        embed:             optional raw embed dict.
    """
    if not _app_id:
        return {"ok": False, "error": "no app id — set DISCORD_APP_ID in .pyfun"}
    if not interaction_token:
        return {"ok": False, "error": "no interaction_token given"}
    if content and len(content) > _MAX_CONTENT_LEN:
        return {"ok": False, "error": f"content exceeds {_MAX_CONTENT_LEN} chars (Discord limit)"}

    payload: Dict[str, Any] = {"content": content}
    if embed:
        payload["embeds"] = [embed]

    path = f"/webhooks/{_app_id}/{interaction_token}"
    return await _api_request("POST", path, payload, auth=False)


# =============================================================================
# SETUP HELPER — slash command registration via REST
# =============================================================================

async def register_commands(commands: list, guild_id: str = "") -> dict:
    """
    Bulk-register slash commands (PUT — overwrites existing set).

    Args:
        commands:  list of Discord command defs, e.g.
                   [{"name": "ask", "description": "Ask the hub",
                     "type": 1,
                     "options": [{"name": "prompt", "type": 3,
                                  "description": "your question",
                                  "required": True}]}]
        guild_id:  optional — guild-scoped = instant propagation (testing).
                   Global commands take up to 1 hour to propagate (Discord rule).

    Returns:
        {"ok": True, "data": <registered commands>} on success.
    """
    if not _active or not _token:
        return {"ok": False, "error": "Discord not active or no bot token"}
    if not _app_id:
        return {"ok": False, "error": "no app id — set DISCORD_APP_ID in .pyfun"}
    if not commands or not isinstance(commands, list):
        return {"ok": False, "error": "commands must be a non-empty list"}

    if guild_id:
        path = f"/applications/{_app_id}/guilds/{guild_id}/commands"
    else:
        path = f"/applications/{_app_id}/commands"

    return await _api_request("PUT", path, commands)


# =============================================================================
# INBOUND — Interactions Endpoint (Discord → hub)
# =============================================================================

def verify_signature(raw_body: bytes, signature: str, timestamp: str) -> bool:
    """
    Ed25519 verification of an incoming Discord interaction.

    Discord signs (timestamp + raw body) with the app's private key;
    we verify against the PUBLIC key from the Dev Portal. Forged or
    replayed requests fail here → route answers 401. This is the ONLY
    thing that makes a public /discord endpoint safe.

    IMPORTANT: the route must pass the RAW request bytes (request.get_data()),
    NOT re-serialized JSON — re-serialization changes one byte and the
    signature dies. Verify BEFORE json.loads, always.

    Args:
        raw_body:  raw request body bytes (exactly as received).
        signature: hex string from X-Signature-Ed25519 header.
        timestamp: string from X-Signature-Timestamp header.

    Returns:
        True = authentic and fresh. False = reject (401), never crash.
    """
    if not _public_key or not signature or not timestamp:
        return False

    # Replay protection — stale timestamp = someone replaying an old request
    try:
        if abs(time.time() - int(timestamp)) > _TIMESTAMP_MAX_AGE:
            logger.warning("Discord: interaction timestamp stale — rejected")
            return False
    except ValueError:
        return False

    try:
        pub_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(_public_key))
        pub_key.verify(
            bytes.fromhex(signature),
            timestamp.encode("utf-8") + raw_body,
        )
        return True
    except (InvalidSignature, ValueError, TypeError) as e:
        logger.warning("Discord: signature invalid (%s) — rejected", type(e).__name__)
        return False


def register_command_handler(name: str, handler: Callable) -> None:
    """
    Map a slash command name to a handler — LATER wiring point for tools.py.

    Handler contract:
        async def handler(interaction: dict) -> dict
        Returns a full Discord response payload, e.g.
        {"type": 4, "data": {"content": "answer"}}         — direct
        {"type": 5}                                        — defer, then
            await discord_api.send_followup(token, answer) — followup

    The built-in "ping" answers without any tools — proves the endpoint.
    """
    if not name or not callable(handler):
        logger.warning("Discord: register_command_handler ignored (bad name/handler)")
        return
    _command_handlers[name] = handler
    logger.info("Discord: command handler registered: %s", name)


async def _builtin_ping(interaction: dict) -> dict:
    """Built-in health command — no tools needed, proves endpoint + config."""
    hub = config.get_hub()
    name = hub.get("HUB_NAME", "Hub")
    return {"type": RESPONSE_CHANNEL_MESSAGE, "data": {"content": f"🏓 {name} is alive"}}


# Ship the builtin — removable via unregister if you hate it
register_command_handler("ping", _builtin_ping)


async def handle_interaction(payload: dict) -> Optional[dict]:
    """
    Process a verified interaction payload → response dict for Discord.

    Handles:
        type 1 (PING)          → {"type": 1}  (PONG — Dev Portal URL check)
        type 2 (APPLICATION_COMMAND) → dispatch to registered handler;
                                       unknown command → honest error message.
        anything else          → None (route answers 400 + logs — extend here
                                       when you need components/modals).

    Args:
        payload: parsed JSON body — only AFTER verify_signature passed.

    Returns:
        Response dict (route: jsonify + 200) or None (route: 400).
    """
    itype = payload.get("type")

    if itype == TYPE_PING:
        logger.info("Discord: PING → PONG (endpoint validation)")
        return {"type": RESPONSE_PONG}

    if itype == TYPE_APPLICATION_COMMAND:
        data = payload.get("data", {})
        name = data.get("name", "")
        handler = _command_handlers.get(name)

        if handler is None:
            logger.warning("Discord: no handler for command '%s'", name)
            return {
                "type": RESPONSE_CHANNEL_MESSAGE,
                "data": {"content": f"Unknown command: {name} — no handler registered."},
            }

        try:
            return await handler(payload)
        except Exception as e:
            # Sanitized — exception type only, never payload details
            logger.warning("Discord: handler '%s' failed: %s", name, type(e).__name__)
            return {
                "type": RESPONSE_CHANNEL_MESSAGE,
                "data": {"content": "Handler error — see hub logs."},
            }

    logger.warning("Discord: unhandled interaction type %s", itype)
    return None
nk v 2.52
