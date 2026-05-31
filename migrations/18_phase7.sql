-- Phase 7: Calendar, stock screen, cost, behavior tables
-- Idempotent: all statements use CREATE TABLE IF NOT EXISTS

PRAGMA foreign_keys = ON;

-- ── Skill ⑤ 投资日历 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS task_calendar (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    category        TEXT NOT NULL
                    CHECK(category IN ('cooldown','earnings','rebalance','monthly','quarterly','annual','custom')),
    due_date        TEXT NOT NULL,          -- ISO date YYYY-MM-DD
    priority        TEXT NOT NULL DEFAULT 'medium'
                    CHECK(priority IN ('high','medium','low')),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','done','skipped','overdue')),
    related_code    TEXT,                   -- stock code if applicable
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_calendar_due ON task_calendar(due_date, status);

CREATE TABLE IF NOT EXISTS task_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER NOT NULL,
    action          TEXT NOT NULL CHECK(action IN ('created','completed','skipped','snoozed','overdue')),
    notes           TEXT,
    logged_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES task_calendar(id) ON DELETE CASCADE
);

-- ── Skill ③ 对话式选股 ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS custom_strategies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    -- Structured criteria (JSON)
    criteria_json   TEXT NOT NULL DEFAULT '{}',
    -- Natural language query that generated this strategy
    source_query    TEXT,
    -- Style tags (JSON array)
    style_tags      TEXT NOT NULL DEFAULT '[]',
    last_run_date   TEXT,
    last_run_count  INTEGER DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Skill ⑦ 交易成本 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cost_model (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    market                  TEXT NOT NULL CHECK(market IN ('A_SH','A_SZ','A_BJ','HK','US')),
    commission_rate         REAL NOT NULL DEFAULT 0.00025,  -- 万2.5
    commission_min          REAL NOT NULL DEFAULT 5.0,      -- 最低5元
    stamp_duty_sell         REAL NOT NULL DEFAULT 0.001,    -- 印花税（卖出）
    stamp_duty_buy          REAL NOT NULL DEFAULT 0.0,      -- 印花税（买入，A股为0）
    transfer_fee_rate       REAL NOT NULL DEFAULT 0.00002,  -- 过户费（沪市）
    transfer_fee_min        REAL NOT NULL DEFAULT 0.0,
    settlement_fee_rate     REAL NOT NULL DEFAULT 0.0,      -- 港股结算费
    platform_fee            REAL NOT NULL DEFAULT 0.0,      -- 港股平台费
    notes                   TEXT,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market)
);

CREATE TABLE IF NOT EXISTS trade_cost_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER,                -- references trades.id if linked
    calc_date       TEXT NOT NULL,
    market          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
    shares          REAL NOT NULL,
    price           REAL NOT NULL,
    gross_amount    REAL NOT NULL,
    commission      REAL NOT NULL,
    stamp_duty      REAL NOT NULL DEFAULT 0.0,
    transfer_fee    REAL NOT NULL DEFAULT 0.0,
    other_fees      REAL NOT NULL DEFAULT 0.0,
    total_cost      REAL NOT NULL,
    net_amount      REAL NOT NULL,
    cost_rate       REAL NOT NULL,          -- total_cost / gross_amount
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Skill ⑨ 行为约束 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS behavior_flags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flag_date       TEXT NOT NULL,
    bias_type       TEXT NOT NULL,          -- e.g. FOMO_BUY, PANIC_SELL, ANCHORING
    related_code    TEXT,
    evidence        TEXT NOT NULL,          -- description of the detected pattern
    severity        TEXT NOT NULL DEFAULT 'medium'
                    CHECK(severity IN ('high','medium','low')),
    acknowledged    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_behavior_flags_date ON behavior_flags(flag_date DESC);

CREATE TABLE IF NOT EXISTS decision_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_date   TEXT NOT NULL,
    related_code    TEXT,
    decision_type   TEXT NOT NULL,          -- BUY, SELL, HOLD, PASS
    stated_reason   TEXT NOT NULL,          -- why the user says they're doing this
    emotion_check   TEXT,                   -- user's self-reported emotion state
    bias_flags      TEXT NOT NULL DEFAULT '[]',  -- JSON array of detected biases
    outcome_date    TEXT,                   -- filled in retrospect
    outcome_notes   TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decision_journal_date ON decision_journal(decision_date DESC);

-- ── Seed default cost models ──────────────────────────────────────────────────
INSERT OR IGNORE INTO cost_model (market, commission_rate, commission_min, stamp_duty_sell, stamp_duty_buy, transfer_fee_rate, notes)
VALUES
    ('A_SH', 0.00025, 5.0, 0.001, 0.0, 0.00002, '沪市A股：万2.5佣金+0.1%印花税(卖)+0.002%过户费'),
    ('A_SZ', 0.00025, 5.0, 0.001, 0.0, 0.0,     '深市A股：万2.5佣金+0.1%印花税(卖)，无过户费'),
    ('A_BJ', 0.00025, 5.0, 0.001, 0.0, 0.0,     '北交所：万2.5佣金+0.1%印花税(卖)'),
    ('HK',   0.0003,  50.0, 0.001, 0.001, 0.0,  '港股：万3佣金+0.1%印花税(双边)+结算费');
