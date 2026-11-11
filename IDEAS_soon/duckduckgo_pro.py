# =============================================================================
# app/providers/gentle_search.py
# GentleSearch Provider — Human-Paced DuckDuckGo Fallback
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# MIT + ESOL 2.1
# Crafted by:  https://github.com/VolkanSah/Multi-LLM-API-Gateway
# Documantation by : AI for better readings in english
# =============================================================================
# ARCHITECTURE NOTE:
#   Anti-spam, anti-scraper-block implementation.
#   Simulates organic human search behavior (burst limits + session cooldowns).
#   Zero scammer tolerance — strict rate pacing.
#   Source idea: Catch Me If You Can! https://github.com/VolkanSah/CMIFC
# =============================================================================
import time
import random
import re
import logging
import asyncio
from urllib.parse import quote_plus
from typing import List, Dict, Any

logger = logging.getLogger("gentle_search")

class GentleSearchProvider(BaseProvider):
    """
    Human-paced fallback search provider with burst control and mandatory cooldown blocks.
    Simulates organic user sessions: 10-15 queries followed by a heavy break.
    """
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]

    def __init__(self, name: str, cfg: dict):
        super().__init__(name, cfg)
        self.query_count = 0
        self.burst_limit = random.randint(10, 15)
        self.cooldown_active_until = 0.0

    async def search(self, query: str) -> Any:
        now = time.time()
        
        if now < self.cooldown_active_until:
            wait_time = int(self.cooldown_active_until - now)
            logger.warning(f"GentleSearch: Burst limit reached. Human session break active. Resets in {wait_time}s.")
            return {"error": "session_cooldown_active", "retry_after": wait_time}

        if self.query_count >= self.burst_limit:
            # Trigger long human break (10 to 15 minutes)
            break_duration = random.randint(600, 900)
            self.cooldown_active_until = now + break_duration
            self.query_count = 0
            logger.info(f"GentleSearch: Completed burst of {self.burst_limit} queries. Taking a {break_duration // 60}m human break.")
            return {"error": "session_cooldown_active", "retry_after": break_duration}

        # Organic intra-query reading/typing delay (4 to 9 seconds)
        await asyncio.sleep(random.uniform(4.0, 9.0))

        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }

        try:
            self.query_count += 1
            response_text = await self._post_or_get(url, headers=headers)
            return self._parse_results(response_text)
        except Exception as e:
            logger.error(f"GentleSearch execution failed: {type(e).__name__}: {e}")
            return {"error": str(e)}

    def _parse_results(self, html: str) -> List[Dict[str, str]]:
        results = []
        result_blocks = re.findall(r'<a class="result__url"[^>]*>(.*?)</a>.*?<a class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        
        for raw_url, raw_snippet in result_blocks[:5]:
            clean_url = re.sub(r'<[^>]+>', '', raw_url).strip()
            clean_snippet = re.sub(r'<[^>]+>', '', raw_snippet).strip()
            if clean_url:
                results.append({"url": clean_url, "snippet": clean_snippet})
                
        return results if results else [{"error": "parsing_failed_or_blocked", "raw_len": len(html)}]
