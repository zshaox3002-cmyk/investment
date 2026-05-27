"""Abstract base class for news sources."""
from __future__ import annotations

from abc import ABC, abstractmethod

from investment.causal.models import RawNews


class NewsSource(ABC):
    """Abstract news source adapter."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique source identifier."""

    @abstractmethod
    def fetch(self, date: str) -> list[RawNews]:
        """Fetch news articles for a given date (YYYY-MM-DD)."""
