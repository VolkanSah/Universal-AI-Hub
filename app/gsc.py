# =============================================================================
# app/gsc.py
# Google Search Console tool — top queries / pages for a property
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# (idea: google-search-console.py)
# =============================================================================
# ARCHITECTURE NOTE:
#   Lives in app/* only. Config from app/.pyfun [GSC]; the credentials come from the
#   ENV var NAMED there (GSC_ENV_KEY) — never from .pyfun, never hard-coded.
#
#   OPTIONAL dependencies (imported lazily — the hub starts fine without them):
#       pip install google-api-python-client google-auth
#
#   Credentials ENV value = the service-account JSON text itself (ideal for HF Space
#   secrets) OR a path to the JSON file. The service account's e-mail must be added as
#   a user in Search Console for the property. Scope is READ-ONLY.
#
#   The Google client is blocking → it runs in a worker thread (asyncio.to_thread),
#   so it never freezes the event loop.
#
# STATUS: shipped INACTIVE (GSC_ACTIVE = "false" in .pyfun).
# =============================================================================
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from . import config

logger = logging.getLogger("gsc")

_SCOPE       = "https://www.googleapis.com/auth/webmasters.readonly"
_DIMENSIONS  = {"query", "page", "country", "device", "date"}
_creds_value = ""     # ENV value (JSON text or file path) — NEVER logged
_active      = False


def initialize() -> None:
    """Read [GSC]. Inactive / no credentials → stays inactive. No crash."""
    global _creds_value, _active
    cfg = config.get_gsc()
    if not cfg or str(cfg.get("GSC_ACTIVE", "false")).lower() != "true":
        logger.info("GSC not active in .pyfun [GSC] — skipped")
        return
    env_key = cfg.get("GSC_ENV_KEY", "GSC_CREDENTIALS")
    _creds_value = (os.getenv(env_key, "") if env_key else "").strip()
    if not _creds_value:
        logger.warning("GSC: %s not set in .env — tool disabled", env_key)
        return
    _active = True
    logger.info("GSC initialized (read-only, default property: %s)", cfg.get("GSC_DEFAULT_PROPERTY") or "none")


def is_ready() -> bool:
    return _active


def _build_service():
    """Blocking: credentials + API client. Lazy imports → clear error if libs are missing."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError("Search Console libs missing — pip install google-api-python-client google-auth")

    if _creds_value.startswith("{"):
        creds = service_account.Credentials.from_service_account_info(json.loads(_creds_value), scopes=[_SCOPE])
    else:
        creds = service_account.Credentials.from_service_account_file(_creds_value, scopes=[_SCOPE])

    cfg = config.get_gsc()
    return build(cfg.get("GSC_API_NAME", "searchconsole"), cfg.get("GSC_API_VERSION", "v1"),
                 credentials=creds, cache_discovery=False)


def _query_sync(property_url: str, days_back: int, row_limit: int, dimension: str) -> List[Dict[str, Any]]:
    service = _build_service()
    # GSC data lags ~2-3 days
    end   = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=days_back + 2)).strftime("%Y-%m-%d")
    body  = {"startDate": start, "endDate": end, "dimensions": [dimension], "rowLimit": row_limit}

    resp = service.searchanalytics().query(siteUrl=property_url, body=body).execute()

    label = "keyword" if dimension == "query" else dimension
    return [
        {
            label:         row["keys"][0],
            "clicks":      row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "ctr":         round(row.get("ctr", 0) * 100, 2),   # in %
            "position":    round(row.get("position", 0), 1),
        }
        for row in resp.get("rows", [])
    ]


async def query(
    property_url: str = "",
    days_back: int = 30,
    row_limit: int = 10,
    dimension: str = "query",
) -> List[Dict[str, Any]]:
    """
    Top queries (or pages/countries/devices/dates) of a Search Console property.

    Args:
        property_url: 'sc-domain:example.com' or 'https://example.com/'. Empty → GSC_DEFAULT_PROPERTY.
        days_back:    1-365 (default 30).
        row_limit:    1-100 (default 10).
        dimension:    query | page | country | device | date.

    Raises:
        ValueError   — bad input.
        RuntimeError — inactive / libs missing / API error (sanitized, no secrets).
    """
    if not _active:
        raise RuntimeError("GSC not active — set GSC_ACTIVE = \"true\" in .pyfun and the credentials in .env")

    prop = (property_url or config.get_gsc().get("GSC_DEFAULT_PROPERTY", "")).strip()
    if not (prop.startswith("sc-domain:") or prop.startswith(("http://", "https://"))):
        raise ValueError("property_url must be 'sc-domain:example.com' or a full https:// URL")
    dimension = str(dimension or "query").lower()
    if dimension not in _DIMENSIONS:
        raise ValueError(f"dimension must be one of {sorted(_DIMENSIONS)}")
    days_back = max(1, min(int(days_back or 30), 365))
    row_limit = max(1, min(int(row_limit or 10), 100))

    try:
        return await asyncio.to_thread(_query_sync, prop, days_back, row_limit, dimension)
    except RuntimeError:
        raise
    except Exception as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        logger.warning("GSC request failed: %s%s", type(e).__name__, f" (HTTP {status})" if status else "")
        raise RuntimeError(f"Search Console request failed: {type(e).__name__}{f' (HTTP {status})' if status else ''}") from None
