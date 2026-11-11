# =============================================================================
# app/auth.py
# Optional shared-token gate for /api, /crypto and the mounted MCP app.
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# =============================================================================
# PRINCIPLE (same as everything here):
#   No token configured = gate OFF = hub behaves exactly as before.
#   Token configured    = /api, /crypto and /mcp need it. "/" (health) stays open.
#
# CONFIG:
#   .pyfun [HUB] HUB_AUTH_ENV = "HUB_API_TOKEN"   → NAME of the ENV var (never the secret)
#   .env / Space secret:  HUB_API_TOKEN=<long random string>
#
# CLIENT sends the token as ONE of:
#   X-Hub-Token: <token>              (use this on private HF Spaces — HF keeps
#                                      Authorization for its own proxy auth)
#   Authorization: Bearer <token>
#
# Pure ASGI, no dependencies → wraps any ASGI app (Quart + mounted MCP).
# =============================================================================
import hmac
import logging
import os

logger = logging.getLogger("auth")


def token_from_env(env_name: str) -> str:
    """Resolve the token from the ENV var NAME configured in .pyfun. Empty = gate off."""
    return (os.getenv(env_name) or "").strip() if env_name else ""


def _candidates(headers) -> list:
    """All tokens the client offered (X-Hub-Token and/or Bearer)."""
    found = []
    for k, v in headers:
        if k == b"x-hub-token":
            found.append(v.decode("latin-1").strip())
        elif k == b"authorization":
            val = v.decode("latin-1").strip()
            if val.lower().startswith("bearer "):
                found.append(val[7:].strip())
    return found


def _authorized(headers, token: str) -> bool:
    expected = token.encode("utf-8")
    return any(
        hmac.compare_digest(c.encode("utf-8"), expected)
        for c in _candidates(headers)
    )


def make_middleware(next_app, token: str, protected_prefixes):
    """
    Wrap an ASGI app. Returns next_app unchanged when no token is configured.
    Only paths equal to / below one of protected_prefixes are gated.
    """
    if not token:
        logger.info("Hub auth: OFF (no HUB_AUTH_ENV token set) — /api and /mcp are open.")
        return next_app

    prefixes = tuple(protected_prefixes)
    logger.info(f"Hub auth: ON for {', '.join(prefixes)}")

    async def middleware(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if any(path == p or path.startswith(p + "/") for p in prefixes):
                if not _authorized(scope.get("headers") or [], token):
                    if scope["type"] == "http":
                        await send({
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"www-authenticate", b"Bearer"),
                            ],
                        })
                        await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                    else:
                        await send({"type": "websocket.close", "code": 1008})
                    return
        await next_app(scope, receive, send)

    return middleware
