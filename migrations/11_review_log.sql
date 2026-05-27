-- Causal review log: audit trail for human decisions on pending edges
-- Phase 3: human review workflow

CREATE TABLE IF NOT EXISTS causal_review_log (
    log_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pending_id          INTEGER REFERENCES pending_edges(pending_id),
    action              TEXT NOT NULL CHECK(action IN ('approve','reject','modify')),
    reason              TEXT,
    modifications_json  TEXT,
    reviewed_at         TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_review_log_pending ON causal_review_log(pending_id);
