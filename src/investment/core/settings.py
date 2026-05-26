"""Path constants and project settings."""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / "src" / "investment"

CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
THESES_DIR = ROOT_DIR / "theses"
TRADES_DIR = ROOT_DIR / "trades"
ALERTS_DIR = ROOT_DIR / "alerts"
REVIEWS_DIR = ROOT_DIR / "reviews"
PROMPTS_DIR = ROOT_DIR / "prompts"
TMP_DIR = ROOT_DIR / "tmp"

DB_PATH = DATA_DIR / "portfolio.db"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SCHEMA_PATH = SRC_DIR / "core" / "schema.sql"

RULES_PATH = CONFIG_DIR / "rules.yaml"
CAPITAL_PATH = CONFIG_DIR / "capital.yaml"
SCREENING_RULES_PATH = CONFIG_DIR / "screening_rules.yaml"

SCHEMA_VERSION = 1
