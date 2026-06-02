-- Phase v3 Agent Orchestrator: new tables
-- Idempotent: all CREATE TABLE use IF NOT EXISTS.
-- ALTER TABLE for task_calendar is handled by migration/_10_agent_v3.py
-- (SQLite has no ADD COLUMN IF NOT EXISTS syntax).

PRAGMA foreign_keys = ON;

-- ── 每日运营状态（健康灯主表）────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_operating_state (
    state_date          TEXT PRIMARY KEY,   -- ISO date YYYY-MM-DD
    health_light        TEXT NOT NULL DEFAULT 'green'
                        CHECK(health_light IN ('green','yellow','red')),
    state_label         TEXT NOT NULL DEFAULT '',
    -- 分层任务计数
    executable_count    INTEGER NOT NULL DEFAULT 0,
    confirm_count       INTEGER NOT NULL DEFAULT 0,
    monitor_count       INTEGER NOT NULL DEFAULT 0,
    blocked_count       INTEGER NOT NULL DEFAULT 0,
    -- 告警计数
    critical_count      INTEGER NOT NULL DEFAULT 0,
    warning_count       INTEGER NOT NULL DEFAULT 0,
    -- 摘要
    top_message         TEXT NOT NULL DEFAULT '',
    evidence_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── 年度目标进度────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS goal_progress (
    progress_date               TEXT PRIMARY KEY,   -- ISO date YYYY-MM-DD
    -- 收益对比
    target_annual_return        REAL,               -- 年度目标收益率（小数，如 0.10）
    actual_ytd_return           REAL,               -- 实际 YTD 收益率
    target_ytd_return           REAL,               -- 按时间线性插值的应达 YTD
    progress_gap                REAL,               -- actual_ytd - target_ytd（正=超额）
    required_return_remaining   REAL,               -- 剩余年份所需年化收益率
    -- 风险
    max_drawdown                REAL,               -- 最大回撤（负数）
    risk_budget_used            REAL,               -- 风险预算使用率（0-1）
    -- 基准
    benchmark_return_ytd        REAL,               -- 基准（沪深300）YTD
    -- 元数据
    portfolio_value             REAL,               -- 计算时总市值
    notes                       TEXT,
    created_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── 持仓健康度────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS position_health (
    calc_date           TEXT NOT NULL,              -- ISO date YYYY-MM-DD
    instrument_id       INTEGER NOT NULL,
    -- 评分
    health_score        REAL,                       -- 0-100 综合健康分
    health_label        TEXT NOT NULL DEFAULT 'unknown'
                        CHECK(health_label IN ('healthy','watch','review','act','unknown','insufficient_data')),
    -- 盈亏
    pnl_pct             REAL,                       -- 持仓盈亏%
    drawdown_pct        REAL,                       -- 相对最高点回撤%（负数）
    -- 仓位
    weight_total        REAL,                       -- 占总组合%
    weight_tranche      REAL,                       -- 占所在档位%
    -- 风险
    risk_contrib_pct    REAL,                       -- 占组合总风险%
    -- 研究质量
    thesis_score        REAL,                       -- 论点评分（0-5）
    alert_count         INTEGER NOT NULL DEFAULT 0, -- 近30日告警数
    -- 建议
    suggested_action    TEXT,
    evidence_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (calc_date, instrument_id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);
CREATE INDEX IF NOT EXISTS idx_position_health_date
    ON position_health(calc_date DESC, instrument_id);

-- ── Agent 运行日志────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_run_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,              -- ISO date YYYY-MM-DD
    mode            TEXT NOT NULL DEFAULT 'premarket'
                    CHECK(mode IN ('premarket','postmarket','manual')),
    started_at      TEXT NOT NULL,              -- ISO datetime
    finished_at     TEXT,                       -- NULL if still running / failed mid
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK(status IN ('running','completed','failed','partial')),
    summary         TEXT NOT NULL DEFAULT '',
    error_message   TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_run_log_date
    ON agent_run_log(run_date DESC, mode);
