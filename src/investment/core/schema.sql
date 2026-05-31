-- Investment portfolio schema v2.0
-- 18 tables + 3 views. See plan: ~/.claude/plans/1-csv-md-precious-lagoon.md

PRAGMA foreign_keys = ON;

-- ==========================================================================
-- Schema version tracking
-- ==========================================================================

CREATE TABLE IF NOT EXISTS schema_version (
  version       INTEGER PRIMARY KEY,
  applied_at    TEXT NOT NULL,
  description   TEXT
);

-- ==========================================================================
-- Dimension tables (manual entry, low churn)
-- ==========================================================================

CREATE TABLE IF NOT EXISTS instruments (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  code          TEXT NOT NULL,
  market        TEXT NOT NULL CHECK(market IN ('A','HK','US','OTC')),
  name          TEXT NOT NULL,
  asset_class   TEXT NOT NULL CHECK(asset_class IN ('STOCK','ETF','BOND','CASH','RSU')),
  industry      TEXT,
  theme         TEXT,
  tranche       TEXT NOT NULL CHECK(tranche IN ('A','B','C','D')),
  price_tick    REAL NOT NULL DEFAULT 0.01,
  active        INTEGER NOT NULL DEFAULT 1,
  notes         TEXT,
  UNIQUE(code, market)
);
CREATE INDEX IF NOT EXISTS idx_instruments_tranche ON instruments(tranche, active);

CREATE TABLE IF NOT EXISTS instrument_themes (
  instrument_id INTEGER NOT NULL,
  theme         TEXT NOT NULL,
  PRIMARY KEY (instrument_id, theme),
  FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
);

-- ==========================================================================
-- Holdings & cash (state, daily updates)
-- ==========================================================================

CREATE TABLE IF NOT EXISTS holdings (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  instrument_id   INTEGER NOT NULL,
  effective_date  TEXT NOT NULL,
  shares          REAL NOT NULL CHECK(shares >= 0),
  cost_price      REAL NOT NULL CHECK(cost_price >= 0),
  added_date      TEXT,
  reason          TEXT,
  source          TEXT NOT NULL DEFAULT 'manual'
                  CHECK(source IN ('manual','trade_apply','migration')),
  UNIQUE(instrument_id, effective_date),
  FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);
CREATE INDEX IF NOT EXISTS idx_holdings_date ON holdings(effective_date DESC);

CREATE TABLE IF NOT EXISTS cash_balances (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  instrument_id   INTEGER NOT NULL,
  effective_date  TEXT NOT NULL,
  balance         REAL NOT NULL,
  annual_rate     REAL,
  status          TEXT,
  notes           TEXT,
  UNIQUE(instrument_id, effective_date),
  FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

-- ==========================================================================
-- Quotes (price time series)
-- ==========================================================================

CREATE TABLE IF NOT EXISTS quotes (
  instrument_id  INTEGER NOT NULL,
  quote_date     TEXT NOT NULL,
  open           REAL,
  high           REAL,
  low            REAL,
  close          REAL NOT NULL CHECK(close >= 0),
  prev_close     REAL,
  change_pct     REAL,
  volume         REAL,
  amount         REAL,
  fetched_at     TEXT NOT NULL,
  source         TEXT NOT NULL DEFAULT 'tencent',
  PRIMARY KEY (instrument_id, quote_date),
  FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);
CREATE INDEX IF NOT EXISTS idx_quotes_date ON quotes(quote_date DESC);

-- ==========================================================================
-- ETF targets vs execution plans (resolves dual-source conflict)
-- ==========================================================================

CREATE TABLE IF NOT EXISTS etf_targets (
  instrument_id    INTEGER PRIMARY KEY,
  target_ratio     REAL NOT NULL CHECK(target_ratio >= 0 AND target_ratio <= 1),
  target_value     REAL NOT NULL,
  rebalance_freq   TEXT NOT NULL
                   CHECK(rebalance_freq IN ('quarterly','monthly','runoff','off')),
  status           TEXT NOT NULL CHECK(status IN ('active','runoff','planned')),
  updated_at       TEXT NOT NULL,
  FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS executions (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_name        TEXT NOT NULL,
  instrument_id    INTEGER NOT NULL,
  phase            TEXT NOT NULL,
  batch            INTEGER NOT NULL DEFAULT 1,
  side             TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
  planned_date     TEXT,
  planned_end      TEXT,
  planned_shares   REAL,
  planned_amount   REAL,
  trigger_type     TEXT
                   CHECK(trigger_type IN ('time','price_abs','price_rel','event','condition')),
  trigger_spec     TEXT,
  status           TEXT NOT NULL DEFAULT 'pending'
                   CHECK(status IN ('pending','in_progress','done','skipped','blocked','expired')),
  result_summary   TEXT,
  log_ref          TEXT,
  notes            TEXT,
  source_doc       TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_executions_key
  ON executions(plan_name, instrument_id, phase, batch, side);
CREATE INDEX IF NOT EXISTS idx_executions_plan ON executions(plan_name, status);
CREATE INDEX IF NOT EXISTS idx_executions_pending
  ON executions(status, planned_date) WHERE status='pending';

-- ==========================================================================
-- Theses & scoring (frontmatter -> theses, body kept by body_path)
-- ==========================================================================

CREATE TABLE IF NOT EXISTS theses (
  instrument_id    INTEGER PRIMARY KEY,
  version          TEXT NOT NULL,
  current_score    REAL,
  rating           TEXT,
  action           TEXT,
  alert_context    TEXT,
  body_path        TEXT NOT NULL,
  next_review_date TEXT,
  updated_at       TEXT NOT NULL,
  FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS thesis_scores (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  instrument_id    INTEGER NOT NULL,
  snapshot_date    TEXT NOT NULL,
  dimension        TEXT NOT NULL,
  score            REAL NOT NULL CHECK(score >= 0 AND score <= 5),
  trend            TEXT,
  rationale        TEXT,
  source           TEXT NOT NULL DEFAULT 'thesis_md',
  UNIQUE(instrument_id, snapshot_date, dimension),
  FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);
CREATE INDEX IF NOT EXISTS idx_thesis_scores_time
  ON thesis_scores(instrument_id, snapshot_date DESC);

-- ==========================================================================
-- Trades, decisions, stop rules
-- ==========================================================================

CREATE TABLE IF NOT EXISTS decisions (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_no      TEXT UNIQUE NOT NULL,
  decision_date    TEXT NOT NULL,
  decision_type    TEXT NOT NULL
                   CHECK(decision_type IN ('NEW','ADD','REDUCE','EXIT','REBALANCE','EMERGENCY')),
  primary_instrument_id INTEGER,
  body_path        TEXT NOT NULL,
  ic_memo_passed   INTEGER NOT NULL DEFAULT 0,
  cooling_until    TEXT,
  status           TEXT NOT NULL DEFAULT 'active'
                   CHECK(status IN ('draft','active','executed','cancelled','superseded')),
  rules_breach_ref TEXT,
  notes            TEXT,
  FOREIGN KEY (primary_instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS trades (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  instrument_id    INTEGER NOT NULL,
  trade_date       TEXT NOT NULL,
  side             TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
  shares           REAL NOT NULL CHECK(shares > 0),
  price            REAL NOT NULL CHECK(price > 0),
  amount           REAL NOT NULL,
  fees             REAL NOT NULL DEFAULT 0,
  decision_id      INTEGER,
  execution_id     INTEGER,
  cooling_compliant INTEGER CHECK(cooling_compliant IN (0,1)),
  notes            TEXT,
  source_doc       TEXT,
  created_at       TEXT NOT NULL,
  FOREIGN KEY (instrument_id) REFERENCES instruments(id),
  FOREIGN KEY (decision_id) REFERENCES decisions(id),
  FOREIGN KEY (execution_id) REFERENCES executions(id)
);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trades_instrument
  ON trades(instrument_id, trade_date DESC);

CREATE TABLE IF NOT EXISTS stop_rules (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id      INTEGER NOT NULL,
  instrument_id    INTEGER NOT NULL,
  rule_type        TEXT NOT NULL CHECK(rule_type IN
                   ('STOP_LOSS','TAKE_PROFIT','GRID_BUY','GRID_SELL',
                    'TRAILING','TIME','THESIS','HARD_DD')),
  trigger_kind     TEXT NOT NULL
                   CHECK(trigger_kind IN ('PRICE_ABS','PRICE_REL','PNL_PCT','EVENT','TIME')),
  trigger_value    REAL,
  trigger_meta     TEXT,
  action           TEXT NOT NULL,
  shares           REAL,
  active_from      TEXT,
  active_until     TEXT,
  priority         INTEGER NOT NULL DEFAULT 100,
  status           TEXT NOT NULL DEFAULT 'armed'
                   CHECK(status IN ('armed','triggered','disarmed','expired')),
  fired_at         TEXT,
  fired_trade_id   INTEGER,
  FOREIGN KEY (decision_id) REFERENCES decisions(id),
  FOREIGN KEY (instrument_id) REFERENCES instruments(id),
  FOREIGN KEY (fired_trade_id) REFERENCES trades(id)
);
CREATE INDEX IF NOT EXISTS idx_stop_rules_armed
  ON stop_rules(instrument_id, status) WHERE status='armed';

-- ==========================================================================
-- Alerts, candidates, reviews, breaches
-- ==========================================================================

CREATE TABLE IF NOT EXISTS alerts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_date       TEXT NOT NULL,
  alert_type       TEXT NOT NULL,
  severity         TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
  instrument_id    INTEGER,
  message          TEXT NOT NULL,
  context_json     TEXT,
  body_path        TEXT,
  acknowledged     INTEGER NOT NULL DEFAULT 0,
  acknowledged_at  TEXT,
  decision_ref     INTEGER,
  FOREIGN KEY (instrument_id) REFERENCES instruments(id),
  FOREIGN KEY (decision_ref) REFERENCES decisions(id)
);
CREATE INDEX IF NOT EXISTS idx_alerts_date ON alerts(alert_date DESC, severity);

CREATE TABLE IF NOT EXISTS candidates (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_date           TEXT NOT NULL,
  code                TEXT NOT NULL,
  market              TEXT NOT NULL,
  name                TEXT NOT NULL,
  industry            TEXT,
  theme               TEXT,
  market_cap          REAL,
  pe_ttm              REAL,
  pb                  REAL,
  ps                  REAL,
  dividend_yield      REAL,
  roe_3y_avg          REAL,
  northbound_trend    TEXT,
  fund_holding_trend  TEXT,
  unlock_pressure     TEXT,
  pillar1_score       REAL,
  pillar2_score       REAL,
  pillar3_score       REAL,
  composite_score     REAL,
  priority            INTEGER,
  compliance_passed   INTEGER NOT NULL DEFAULT 0,
  compliance_blocked_by TEXT,
  status              TEXT NOT NULL DEFAULT 'candidate'
                      CHECK(status IN ('candidate','researching','ic_memo','accepted','rejected','expired')),
  notes               TEXT,
  source_scan         TEXT,
  UNIQUE(scan_date, code, market)
);
CREATE INDEX IF NOT EXISTS idx_candidates_priority
  ON candidates(scan_date DESC, priority);

CREATE TABLE IF NOT EXISTS trade_reviews (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  review_date      TEXT NOT NULL,
  scope            TEXT NOT NULL CHECK(scope IN ('trade','decision','monthly','quarterly','event')),
  trade_id         INTEGER,
  decision_id      INTEGER,
  result_pnl       REAL,
  result_pnl_pct   REAL,
  outcome          TEXT NOT NULL CHECK(outcome IN ('win','loss','break_even','partial')),
  body_path        TEXT NOT NULL,
  emotion_record   TEXT,
  rule_breach      INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (trade_id) REFERENCES trades(id),
  FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS review_errors (
  review_id        INTEGER NOT NULL,
  error_code       TEXT NOT NULL CHECK(error_code IN (
                   'EMOTIONAL_AVERAGING_DOWN',
                   'CHASE_HIGH',
                   'PANIC_SELL',
                   'IGNORE_THESIS_BREAK',
                   'OVERSIZE_POSITION',
                   'COOLING_PERIOD_VIOLATION',
                   'MISSING_IC_MEMO',
                   'STOP_LOSS_OVERRIDE',
                   'OVERTRADE',
                   'NARRATIVE_DRIFT',
                   'CONFIRMATION_BIAS',
                   'OTHER'
                   )),
  severity         TEXT NOT NULL CHECK(severity IN ('low','medium','high','critical')),
  detail           TEXT,
  PRIMARY KEY (review_id, error_code),
  FOREIGN KEY (review_id) REFERENCES trade_reviews(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rule_breaches (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_path        TEXT NOT NULL,
  instrument_id    INTEGER,
  current_value    REAL NOT NULL,
  threshold        REAL NOT NULL,
  breach_amount    REAL NOT NULL,
  detected_at      TEXT NOT NULL,
  grace_period_expires TEXT,
  status           TEXT NOT NULL CHECK(status IN ('active','remediating','resolved','escalated')),
  resolution_decision_id INTEGER,
  notes            TEXT,
  FOREIGN KEY (instrument_id) REFERENCES instruments(id),
  FOREIGN KEY (resolution_decision_id) REFERENCES decisions(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_breaches_key
  ON rule_breaches(rule_path, COALESCE(instrument_id, 0), detected_at);

-- ==========================================================================
-- Views (single-source-of-truth derivations)
-- ==========================================================================

DROP VIEW IF EXISTS v_portfolio_snapshot;
CREATE VIEW v_portfolio_snapshot AS
SELECT
  i.id, i.code, i.market, i.name, i.tranche, i.industry, i.theme,
  h.shares,
  h.cost_price,
  q.close                                             AS price,
  h.shares * q.close                                  AS market_value,
  h.shares * h.cost_price                             AS cost_total,
  CASE WHEN h.cost_price > 0
       THEN (q.close - h.cost_price) / h.cost_price
       ELSE 0 END                                     AS pnl_pct,
  q.quote_date                                        AS as_of
FROM instruments i
JOIN holdings h ON h.instrument_id = i.id
  AND h.effective_date = (
    SELECT MAX(effective_date) FROM holdings h2 WHERE h2.instrument_id = i.id
  )
JOIN quotes q ON q.instrument_id = i.id
  AND q.quote_date = (
    SELECT MAX(quote_date) FROM quotes q2 WHERE q2.instrument_id = i.id
  )
WHERE i.active = 1;

DROP VIEW IF EXISTS v_compliance_status;
CREATE VIEW v_compliance_status AS
SELECT
  rb.id, rb.rule_path, rb.current_value, rb.threshold,
  rb.current_value - rb.threshold AS breach_amount,
  rb.status, rb.grace_period_expires,
  CASE
    WHEN rb.grace_period_expires IS NULL THEN NULL
    WHEN date(rb.grace_period_expires) < date('now') THEN 'EXPIRED'
    WHEN julianday(rb.grace_period_expires) - julianday('now') < 7 THEN 'URGENT'
    ELSE 'OK'
  END AS urgency,
  i.code, i.name
FROM rule_breaches rb
LEFT JOIN instruments i ON i.id = rb.instrument_id
WHERE rb.status IN ('active','remediating');

DROP VIEW IF EXISTS v_pending_executions;
CREATE VIEW v_pending_executions AS
SELECT
  e.id, e.plan_name, e.phase, e.batch, e.side,
  e.planned_date, e.planned_end, e.planned_shares, e.planned_amount,
  e.trigger_type, e.trigger_spec, e.status, e.notes,
  i.code, i.name, i.tranche
FROM executions e
JOIN instruments i ON i.id = e.instrument_id
WHERE e.status IN ('pending','in_progress')
ORDER BY
  CASE WHEN e.planned_date IS NULL THEN 1 ELSE 0 END,
  e.planned_date;
