"""Load rules.yaml and capital.yaml into typed dicts."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from investment.core.settings import CAPITAL_PATH, RULES_PATH


@lru_cache(maxsize=1)
def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def load_capital(path: Path = CAPITAL_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def reload_rules() -> dict[str, Any]:
    """Force reload (clears lru_cache)."""
    load_rules.cache_clear()
    load_capital.cache_clear()
    return load_rules()
