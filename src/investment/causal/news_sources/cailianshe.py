"""财联社 7×24 快讯 — akshare adapter."""
from __future__ import annotations

import hashlib
from datetime import date as dt_date

from .base import NewsSource
from investment.causal.models import RawNews


class CailiansheSource(NewsSource):
    """財联社实时快讯 via akshare ``stock_news_main_cx``."""

    name = "cailianshe"

    def fetch(self, date: str) -> list[RawNews]:
        """Fetch 财联社快讯 for a given date."""
        try:
            import akshare as ak
        except ImportError:
            return []

        try:
            df = ak.stock_news_main_cx()
        except Exception:
            return []

        if df is None or df.empty:
            return []

        results: list[RawNews] = []
        max_articles = self.config.get("max_articles_per_source", 50)

        for _, row in df.head(max_articles).iterrows():
            title = str(row.get("title", row.get("content", "")))
            content = str(row.get("content", ""))
            url = str(row.get("url", ""))

            if not title:
                continue

            content_hash = hashlib.sha256(
                (title + content).encode("utf-8")
            ).hexdigest()

            results.append(RawNews(
                title=title[:200],
                content=content[:500],
                url=url,
                source=self.name,
                date=date,
                content_hash=content_hash,
            ))

        return results
