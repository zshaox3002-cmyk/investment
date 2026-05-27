-- Phase 5: Chain assessment storage for daily holding impact reports
-- Creates chain_assessments table for per-holding impact assessment results.
-- UNIQUE(date, holding_code) ensures idempotent re-runs.

CREATE TABLE IF NOT EXISTS chain_assessments (
    assessment_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT NOT NULL,
    holding_code        TEXT NOT NULL,
    impact_score        REAL,
    impact_level        TEXT CHECK(impact_level IN ('L1','L2','L3','L4','L5')),
    direction           TEXT CHECK(direction IN ('positive','negative','neutral')),
    paths_json          TEXT,
    triggering_signal_ids TEXT,
    narrative_md        TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, holding_code)
);

CREATE INDEX IF NOT EXISTS idx_assess_date ON chain_assessments(date);
CREATE INDEX IF NOT EXISTS idx_assess_holding ON chain_assessments(holding_code);
