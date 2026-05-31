-- Phase 4: Risk quantification tables
-- risk_metrics, correlation_matrix, risk_contribution
-- Idempotent: all statements use CREATE TABLE IF NOT EXISTS

PRAGMA foreign_keys = ON;

-- ── Portfolio-level risk metrics (one row per calculation date) ───────────────
CREATE TABLE IF NOT EXISTS risk_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    calc_date           TEXT NOT NULL,
    lookback_days       INTEGER NOT NULL DEFAULT 252,
    portfolio_vol       REAL,   -- annualised volatility (decimal, e.g. 0.18)
    max_drawdown        REAL,   -- max drawdown from peak (negative decimal)
    dd_duration_days    INTEGER,-- drawdown duration in calendar days
    var_95              REAL,   -- 1-day 95% VaR (negative decimal)
    var_99              REAL,   -- 1-day 99% VaR (negative decimal)
    sharpe_ratio        REAL,   -- annualised Sharpe (risk-free = 0)
    instrument_count    INTEGER,-- number of instruments in calculation
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(calc_date, lookback_days)
);
CREATE INDEX IF NOT EXISTS idx_risk_metrics_date ON risk_metrics(calc_date DESC);

-- ── Pairwise correlation matrix (one row per instrument pair per date) ────────
CREATE TABLE IF NOT EXISTS correlation_matrix (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    calc_date       TEXT NOT NULL,
    instrument_id_a INTEGER NOT NULL,
    instrument_id_b INTEGER NOT NULL,
    corr_value      REAL NOT NULL,  -- Pearson correlation [-1, 1]
    lookback_days   INTEGER NOT NULL DEFAULT 60,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(calc_date, instrument_id_a, instrument_id_b, lookback_days),
    FOREIGN KEY (instrument_id_a) REFERENCES instruments(id),
    FOREIGN KEY (instrument_id_b) REFERENCES instruments(id)
);
CREATE INDEX IF NOT EXISTS idx_corr_date ON correlation_matrix(calc_date DESC);

-- ── Per-instrument risk contribution ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS risk_contribution (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    calc_date           TEXT NOT NULL,
    instrument_id       INTEGER NOT NULL,
    weight              REAL NOT NULL,  -- portfolio weight (decimal)
    vol                 REAL,           -- individual annualised vol
    risk_contrib_pct    REAL NOT NULL,  -- % of total portfolio risk
    marginal_contrib    REAL,           -- marginal risk contribution
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(calc_date, instrument_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);
CREATE INDEX IF NOT EXISTS idx_risk_contrib_date ON risk_contribution(calc_date DESC);
