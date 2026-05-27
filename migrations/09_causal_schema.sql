-- Causal graph schema: nodes, edges, and views
-- Phase 1: foundation tables for event-driven causal reasoning

CREATE TABLE IF NOT EXISTS causal_nodes (
    node_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    node_type       TEXT NOT NULL CHECK(node_type IN ('event','macro','commodity','sector','holding','policy')),
    layer           TEXT NOT NULL CHECK(layer IN ('L0_geopolitical','L1_macro','L2_industry','L3_holding')),
    description     TEXT,
    keywords        TEXT,
    activation_score REAL DEFAULT 0.0,
    last_signal_at  TEXT,
    signal_count_30d INTEGER DEFAULT 0,
    lifecycle_state TEXT DEFAULT 'active' CHECK(lifecycle_state IN ('active','dormant','archived')),
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_causal_nodes_lifecycle ON causal_nodes(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_causal_nodes_type ON causal_nodes(node_type);

CREATE TABLE IF NOT EXISTS causal_edges (
    edge_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id    INTEGER NOT NULL REFERENCES causal_nodes(node_id),
    to_node_id      INTEGER NOT NULL REFERENCES causal_nodes(node_id),
    direction       INTEGER NOT NULL CHECK(direction IN (-1, 1)),
    d1_directness   INTEGER CHECK(d1_directness BETWEEN 1 AND 5),
    d2_elasticity   INTEGER CHECK(d2_elasticity BETWEEN 1 AND 5),
    d3_consistency  INTEGER CHECK(d3_consistency BETWEEN 1 AND 5),
    d4_speed        INTEGER CHECK(d4_speed BETWEEN 1 AND 5),
    d5_uniqueness   INTEGER CHECK(d5_uniqueness BETWEEN 1 AND 5),
    strength        REAL,
    lag_days        INTEGER DEFAULT 0,
    evidence_summary TEXT,
    evidence_urls   TEXT,
    approved_by     TEXT,
    approved_at     TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(from_node_id, to_node_id)
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON causal_edges(from_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON causal_edges(to_node_id);

DROP VIEW IF EXISTS v_causal_edges_full;
CREATE VIEW v_causal_edges_full AS
SELECT
    e.edge_id,
    n1.name AS from_name, n1.layer AS from_layer,
    n2.name AS to_name,   n2.layer AS to_layer,
    e.direction, e.strength, e.lag_days,
    e.d1_directness, e.d2_elasticity, e.d3_consistency, e.d4_speed, e.d5_uniqueness,
    e.evidence_summary
FROM causal_edges e
JOIN causal_nodes n1 ON e.from_node_id = n1.node_id
JOIN causal_nodes n2 ON e.to_node_id = n2.node_id;
