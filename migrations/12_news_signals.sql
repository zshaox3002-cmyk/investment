-- News signals and node activation tracking
-- Phase 4: daily signal scan and activation decay

CREATE TABLE IF NOT EXISTS news_signals (
    signal_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT NOT NULL,
    source              TEXT NOT NULL,
    url                 TEXT,
    title               TEXT NOT NULL,
    summary             TEXT,
    affected_node_ids   TEXT NOT NULL,
    signal_strength     REAL CHECK(signal_strength BETWEEN -1 AND 1),
    confidence          REAL CHECK(confidence BETWEEN 0 AND 1),
    raw_content_hash    TEXT UNIQUE,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_signals_date ON news_signals(date);

CREATE TABLE IF NOT EXISTS node_activation_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER REFERENCES causal_nodes(node_id),
    date        TEXT NOT NULL,
    delta       REAL,
    new_score   REAL,
    reason      TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_activation_log_node ON node_activation_log(node_id, date);
