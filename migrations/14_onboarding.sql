-- Phase 2: Onboarding tables
-- user_profile, goals, asset_inventory
-- Idempotent: all statements use CREATE TABLE IF NOT EXISTS

PRAGMA foreign_keys = ON;

-- ── User profile ──────────────────────────────────────────────────────────────
-- Stores the investor's risk tolerance, horizon, and capital.
-- Only one active profile at a time (latest by created_at).
CREATE TABLE IF NOT EXISTS user_profile (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_tolerance          TEXT NOT NULL
                            CHECK(risk_tolerance IN ('conservative','moderate','aggressive')),
    max_drawdown_tolerance  REAL NOT NULL CHECK(max_drawdown_tolerance > 0 AND max_drawdown_tolerance <= 100),
    horizon_years           INTEGER NOT NULL CHECK(horizon_years >= 1 AND horizon_years <= 50),
    investable_capital      REAL NOT NULL CHECK(investable_capital > 0),
    -- Generated A/B/C ratios (stored as decimals, e.g. 0.25)
    a_ratio                 REAL NOT NULL DEFAULT 0.25,
    b_ratio                 REAL NOT NULL DEFAULT 0.50,
    c_ratio                 REAL NOT NULL DEFAULT 0.25,
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Investment goals ──────────────────────────────────────────────────────────
-- One or more goals per profile (e.g. retirement, house down-payment).
CREATE TABLE IF NOT EXISTS goals (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id              INTEGER NOT NULL,
    name                    TEXT NOT NULL DEFAULT '主要投资目标',
    target_annual_return    REAL NOT NULL CHECK(target_annual_return > 0 AND target_annual_return <= 100),
    target_amount           REAL,
    deadline                TEXT,   -- ISO date YYYY-MM-DD
    status                  TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active','achieved','abandoned')),
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES user_profile(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_goals_profile ON goals(profile_id, status);

-- ── Asset inventory ───────────────────────────────────────────────────────────
-- Snapshot of the user's assets at onboarding time (or when updated).
CREATE TABLE IF NOT EXISTS asset_inventory (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id              INTEGER NOT NULL,
    asset_type              TEXT NOT NULL
                            CHECK(asset_type IN ('stock','etf','bond','cash','fund','rsu','other')),
    amount                  REAL NOT NULL CHECK(amount >= 0),
    account                 TEXT,   -- e.g. '招商证券', '余额宝', '银行活期'
    currency                TEXT NOT NULL DEFAULT 'CNY',
    notes                   TEXT,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile_id) REFERENCES user_profile(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_inventory_profile ON asset_inventory(profile_id);
