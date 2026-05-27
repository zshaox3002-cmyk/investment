"""华尔街见闻 RSS adapter."""
from __future__ import annotations

import hashlib

from .base import NewsSource
from investment.causal.models import RawNews


class WallstreetcnSource(NewsSource):
    """华尔街见闻 RSS feed."""

    name = "wallstreetcn"

    def fetch(self, date: str) -> list[RawNews]:
        """Fetch from RSS. Falls back gracefully on network errors."""
        url = self.config.get("url", "")
        if not url:
            return []

        try:
            import xml.etree.ElementTree as ET
            from urllib.request import urlopen, Request
        except ImportError:
            return []

        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=15) as resp:
                tree = ET.parse(resp)
        except Exception:
            return []

        results: list[RawNews] = []
        max_articles = self.config.get("max_articles_per_source", 50)
        root = tree.getroot()
        items = root.findall(".//item")

        for item in items[:max_articles]:
            title_el = item.find("title")
            desc_el = item.find("description")
            link_el = item.find("link")

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            content = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
            link = link_el.text.strip() if link_el is not None and link_el.text else ""

            if not title:
                continue

            content_hash = hashlib.sha256(
                (title + content).encode("utf-8")
            ).hexdigest()

            results.append(RawNews(
                title=title[:200],
                content=content[:500],
                url=link,
                source=self.name,
                date=date,
                content_hash=content_hash,
            ))

        return results
