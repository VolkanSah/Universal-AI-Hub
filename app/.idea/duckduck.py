# for search fallback
class GentleSearchProvider(BaseProvider):
    """
    Sicherer Such-Provider, der Google nicht provoziert.
    """
    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.last_call = 0
        self.min_interval = 300  # 5 Minuten Pause!

    async def search(self, query: str):
        now = time.time()
        if now - self.last_call < self.min_interval:
            logger.warning("GentleSearch: Zu früh! Wir schützen unsere IP.")
            return "Wait for cooldown..."

        # Wir nutzen DuckDuckGo als Puffer /fallback
        url = f"https://html.duckduckgo.com/html/?q={query}"
        
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
            "DNT": "1", # Do Not Track 
        }

        try:
            # wir nutzen am besten  meine CMIFC-Logik, aber ohne Aggression (CMIYC)
            data = await self._post_or_get(url, headers=headers)
            self.last_call = time.time()
            return self._parse_simple_results(data)
        except Exception as e:
            return f"Error: {e}"
