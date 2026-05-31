"""News source adapters for causal signal scanning."""
from .base import NewsSource
from .cailianshe import CailiansheSource
from .wallstreetcn import WallstreetcnSource

__all__ = ["NewsSource", "CailiansheSource", "WallstreetcnSource"]
