-- Phase 5: Performance attribution tables
-- performance_attribution, benchmark_quotes
-- Idempotent: all statements use CREATE TABLE IF NOT EXISTS

PRAGMA foreign_keys = ON;

-- ── Benchmark price history ───────────────────────────────────────────────────
-- Stores daily close prices for benchmark indices (e.g. 沪深300 = 000300).
CREATE TABLE IF NOT EXISTS benchmark_quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,          -- e.g. '000300'
    name            TEXT NOT NULL,          -- e.g. '沪深300'
    quote_date      TEXT NOT NULL,
    close           REAL NOT NULL CHECK(close > 0),
    fetched_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, quote_date)
);
CREATE INDEX IF NOT EXISTS idx_bq_code_date ON benchmark_quotes(code, quote_date DESC);

-- ── Performance attribution results ──────────────────────────────────────────
-- One row per attribution period.
-- Brinson-Hood-Beebower decomposition:
--   total_return = timing_contrib + selection_contrib + allocation_contrib + interaction_contrib
CREATE TABLE IF NOT EXISTS performance_attribution (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start            TEXT NOT NULL,
    period_end              TEXT NOT NULL,
    benchmark_code          TEXT NOT NULL DEFAULT '000300',
    -- Returns (decimal, e.g. 0.12 = 12%)
    total_return            REAL NOT NULL,
    benchmark_return        REAL NOT NULL,
    excess_return           REAL NOT NULL,  -- total_return - benchmark_return
    -- BHB decomposition
    timing_contrib          REAL NOT NULL,  -- market timing (allocation effect)
    selection_contrib       REAL NOT NULL,  -- stock selection effect
    allocation_contrib      REAL NOT NULL,  -- asset allocation effect
    interaction_contrib     REAL NOT NULL DEFAULT 0.0,  -- interaction term
    -- Metadata
    instrument_count        INTEGER,
    data_days               INTEGER,
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(period_start, period_end, benchmark_code)
);
CREATE INDEX IF NOT EXISTS idx_pa_period ON performance_attribution(period_end DESC);
