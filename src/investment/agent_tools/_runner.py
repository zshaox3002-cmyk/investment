"""Internal subprocess runner for inv CLI commands."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_VENV_INV = Path(__file__).resolve().parents[4] / ".venv" / "bin" / "inv"


def run_inv(*args: str, timeout: int = 120) -> tuple[bool, str]:
    """Run `inv <args>` and return (success, combined_output).

    Uses the project venv's inv binary. Falls back to sys.executable -m
    investment.cli if the binary is not found (e.g. editable install without
    the script entry point).
    """
    if _VENV_INV.exists():
        cmd = [str(_VENV_INV), *args]
    else:
        cmd = [sys.executable, "-m", "investment.cli", *args]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"命令超时（>{timeout}s）：inv {' '.join(args)}"
    except Exception as exc:
        return False, f"命令执行失败：{exc}"
