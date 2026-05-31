-- Pending edges: AI-proposed causal links awaiting human review
-- Phase 2: causal-discoverer output staging table

CREATE TABLE IF NOT EXISTS pending_edges (
    pending_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_name          TEXT NOT NULL,
    to_node_name            TEXT NOT NULL,
    from_node_proposed_type TEXT,
    from_node_proposed_layer TEXT,
    to_node_proposed_type   TEXT,
    to_node_proposed_layer  TEXT,
    direction               INTEGER NOT NULL,
    d1_directness           INTEGER,
    d2_elasticity           INTEGER,
    d3_consistency          INTEGER,
    d4_speed                INTEGER,
    d5_uniqueness           INTEGER,
    lag_days                INTEGER,
    confidence              REAL CHECK(confidence BETWEEN 0 AND 1),
    evidence_summary        TEXT,
    evidence_urls           TEXT,
    triggered_by_event      TEXT,
    status                  TEXT DEFAULT 'pending'
                            CHECK(status IN ('pending','approved','rejected','modified')),
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
    decided_at              TEXT,
    decided_by              TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_edges(status);
