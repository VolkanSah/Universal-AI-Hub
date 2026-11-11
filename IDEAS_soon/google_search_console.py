# =============================================================================
# app/tools/google_search_console.py
# Google Search Console Provider — Official API Client
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# MIT
# Repo: https://github.com/VolkanSah/Multi-LLM-API-Gateway
# =============================================================================
# ARCHITECTURE NOTE:
#   Modular tool living in app/* only.
#   Config comes from app/.pyfun [TOOL.gsc] via app/config.py.
#   Credentials loaded via environment variables / Service Account JSON key.
# =============================================================================

import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Union

from google.oauth2 import service_account
from googleapiclient.discovery import build

from . import config

logger = logging.getLogger("gsc_provider")

_service = None
_initialized = False

async def initialize() -> None:
    """Initialize Google Search Console API client from config."""
    global _service, _initialized

    tools_cfg = config.get_active_tools()
    cfg = tools_cfg.get("google_search_console", {})

    if not cfg or cfg.get("active", "false").lower() != "true":
        logger.info("Google Search Console provider not active in .pyfun — skipped")
        return

    env_key = cfg.get("gsc_env_key", "GSC_CREDENTIALS_JSON")
    json_path = os.getenv(env_key) or cfg.get("gsc_credentials_path", "credentials.json")

    try:
        if json_path and Path(json_path).exists():
            creds = service_account.Credentials.from_service_account_file(
                json_path,
                scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            )
        else:
            # Fallback to inline json env if provided
            creds_info = json.loads(os.getenv("GSC_CREDENTIALS_DICT", "{}"))
            if not creds_info:
                logger.warning("GSC: Credentials file not found and no inline dict provided.")
                return
            creds = service_account.Credentials.from_service_account_info(
                creds_info,
                scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            )

        _service = build("webmasters", "v3", credentials=creds)
        _initialized = True
        logger.info("Google Search Console provider initialized successfully.")
    except Exception as e:
        logger.error(f"GSC initialization failed: {type(e).__name__}: {e}")

async def get_search_console_data(
    property_url: str, days_back: int = 30, row_limit: int = 10
) -> Union[List[Dict[str, Any]], str]:
    """Retrieve top search queries (keywords) for a specified property via GSC API."""
    if not _initialized or not _service:
        return "Error: Google Search Console provider not initialized."

    end_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back + 2)).strftime("%Y-%m-%d")

    request_body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query"],
        "rowLimit": row_limit,
    }

    try:
        response = (
            _service.searchanalytics()
            .query(siteUrl=property_url, body=request_body)
            .execute()
        )
        rows = response.get("rows", [])

        formatted_data = []
        for row in rows:
            formatted_data.append(
                {
                    "keyword": row["keys"][0],
                    "clicks": row["clicks"],
                    "impressions": row["impressions"],
                    "ctr": round(row["ctr"] * 100, 2),
                    "position": round(row["position"], 1),
                }
            )

        return formatted_data

    except Exception as e:
        logger.error(f"GSC query failed: {type(e).__name__}: {e}")
        return f"Fehler bei der API-Abfrage: {str(e)}"
