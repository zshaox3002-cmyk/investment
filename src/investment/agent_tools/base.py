"""Base types for agent tool results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Unified return type for all agent tool wrappers.

    Attributes:
        success: True if the underlying command completed without error.
        data: Structured output for programmatic consumption by Skills.
        human_message: Plain-language summary ending with "所以你该做什么".
        raw_output: Raw stdout/stderr from the CLI call (for debugging).
    """
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    human_message: str = ""
    raw_output: str = ""

    def __bool__(self) -> bool:
        return self.success
