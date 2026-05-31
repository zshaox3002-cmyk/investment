"""Causal review workflow: approve, reject, or modify pending edges.

Usage::

    from investment.causal.reviewer import Reviewer
    rev = Reviewer()
    rev.approve(pending_id=1)
    rev.reject(pending_id=2, reason="传导路径不成立")
"""
from __future__ import annotations

import json
from pathlib import Path

from .repo import CausalRepo, _utcnow
from .models import PendingEdge, CausalReviewLog


class Reviewer:
    """Human review workflow for pending causal edges."""

    def __init__(self, db_path: Path | None = None):
        self._repo = CausalRepo(db_path)

    def list_pending(self) -> list[PendingEdge]:
        """List all pending edges awaiting review."""
        with self._repo.transaction():
            return self._repo.list_pending(status="pending")

    def approve(self, pending_id: int, reviewer: str = "human") -> int:
        """Approve a pending edge.

        1. Creates new nodes if they don't exist (using proposed type/layer)
        2. Creates the edge in causal_edges
        3. Updates pending status to 'approved'
        4. Writes review log

        Returns the new edge_id.
        """
        with self._repo.transaction():
            p = self._repo.get_pending(pending_id)
            if not p:
                raise ValueError(f"Pending edge #{pending_id} not found")
            if p.status != "pending":
                raise ValueError(f"Pending edge #{pending_id} already {p.status}")

            # Ensure both nodes exist, creating new ones if needed
            self._ensure_node(
                p.from_node_name, p.from_node_proposed_type,
                p.from_node_proposed_layer,
            )
            self._ensure_node(
                p.to_node_name, p.to_node_proposed_type,
                p.to_node_proposed_layer,
            )

            # Create the edge in causal_edges
            try:
                edge_id = self._repo.add_edge(
                    from_name=p.from_node_name,
                    to_name=p.to_node_name,
                    direction=p.direction,
                    d1=p.d1_directness or 3,
                    d2=p.d2_elasticity or 3,
                    d3=p.d3_consistency or 3,
                    d4=p.d4_speed or 3,
                    d5=p.d5_uniqueness or 3,
                    lag_days=p.lag_days,
                    evidence_summary=p.evidence_summary,
                    evidence_urls=p.evidence_urls,
                    approved_by=reviewer,
                )
            except ValueError as exc:
                if "already exists" in str(exc):
                    raise ValueError(
                        f"Cannot approve: edge '{p.from_node_name}' → "
                        f"'{p.to_node_name}' already exists in causal_edges"
                    ) from exc
                raise

            # Update pending status
            self._repo._conn.execute(
                """UPDATE pending_edges SET status='approved', decided_at=?, decided_by=?
                   WHERE pending_id=?""",
                (_utcnow(), reviewer, pending_id),
            )

            # Write review log
            self._write_log(pending_id, "approve")

            return edge_id

    def reject(self, pending_id: int, reason: str = "", reviewer: str = "human") -> None:
        """Reject a pending edge."""
        with self._repo.transaction():
            p = self._repo.get_pending(pending_id)
            if not p:
                raise ValueError(f"Pending edge #{pending_id} not found")
            if p.status != "pending":
                raise ValueError(f"Pending edge #{pending_id} already {p.status}")

            self._repo._conn.execute(
                """UPDATE pending_edges SET status='rejected', decided_at=?, decided_by=?
                   WHERE pending_id=?""",
                (_utcnow(), reviewer, pending_id),
            )
            self._write_log(pending_id, "reject", reason)

    def modify(
        self,
        pending_id: int,
        reviewer: str = "human",
        reason: str = "",
        **fields,
    ) -> int:
        """Modify a pending edge's scores/fields then approve it.

        Allowed fields: d1, d2, d3, d4, d5, direction, lag_days,
                       evidence_summary, confidence.

        Records the before/after diff in review_log.modifications_json.
        Returns the new edge_id.
        """
        with self._repo.transaction():
            p = self._repo.get_pending(pending_id)
            if not p:
                raise ValueError(f"Pending edge #{pending_id} not found")
            if p.status != "pending":
                raise ValueError(f"Pending edge #{pending_id} already {p.status}")

            # Map field names to their values before modification
            field_map = {
                "d1": ("d1_directness", p.d1_directness),
                "d2": ("d2_elasticity", p.d2_elasticity),
                "d3": ("d3_consistency", p.d3_consistency),
                "d4": ("d4_speed", p.d4_speed),
                "d5": ("d5_uniqueness", p.d5_uniqueness),
                "direction": ("direction", p.direction),
                "lag_days": ("lag_days", p.lag_days),
                "evidence_summary": ("evidence_summary", p.evidence_summary),
                "confidence": ("confidence", p.confidence),
            }

            diff = {}
            for key, value in fields.items():
                if key in field_map:
                    col_name, old_val = field_map[key]
                    diff[col_name] = {"before": old_val, "after": value}

            # Build UPDATE for the modified fields
            set_clauses = []
            params: list = []
            for key, value in fields.items():
                if key in field_map:
                    col_name, _ = field_map[key]
                    set_clauses.append(f"{col_name} = ?")
                    params.append(value)

            if not set_clauses:
                # Nothing modified — still approve (inline, no nested transaction)
                self._repo._conn.execute(
                    "UPDATE pending_edges SET status='approved', decided_at=?, decided_by=? WHERE pending_id=?",
                    (_utcnow(), reviewer, pending_id),
                )
                self._write_log(pending_id, "approve")
                return self._approve_modified(p, reviewer, {})

            set_clauses.append("status = 'modified'")
            set_clauses.append("decided_at = ?")
            set_clauses.append("decided_by = ?")
            params.append(_utcnow())
            params.append(reviewer)
            params.append(pending_id)

            self._repo._conn.execute(
                f"UPDATE pending_edges SET {', '.join(set_clauses)} WHERE pending_id = ?",
                params,
            )

            # Write review log with modifications
            self._write_log(pending_id, "modify", reason, diff)

            # Now approve the modified edge
            return self._approve_modified(p, reviewer, fields)

    def _approve_modified(
        self, p: PendingEdge, reviewer: str, overrides: dict
    ) -> int:
        """Approve a modified pending edge with overridden fields."""
        self._ensure_node(
            p.from_node_name, p.from_node_proposed_type, p.from_node_proposed_layer,
        )
        self._ensure_node(
            p.to_node_name, p.to_node_proposed_type, p.to_node_proposed_layer,
        )

        d1 = overrides.get("d1", p.d1_directness or 3)
        d2 = overrides.get("d2", p.d2_elasticity or 3)
        d3 = overrides.get("d3", p.d3_consistency or 3)
        d4 = overrides.get("d4", p.d4_speed or 3)
        d5 = overrides.get("d5", p.d5_uniqueness or 3)
        direction = overrides.get("direction", p.direction)
        lag = overrides.get("lag_days", p.lag_days)
        evidence = overrides.get("evidence_summary", p.evidence_summary)

        edge_id = self._repo.add_edge(
            from_name=p.from_node_name,
            to_name=p.to_node_name,
            direction=direction,
            d1=d1, d2=d2, d3=d3, d4=d4, d5=d5,
            lag_days=lag,
            evidence_summary=evidence,
            evidence_urls=p.evidence_urls,
            approved_by=reviewer,
        )
        return edge_id

    def _ensure_node(
        self, name: str, node_type: str | None, layer: str | None
    ) -> None:
        """Create a node if it doesn't exist. Requires proposed type/layer for new."""
        if self._repo.node_exists(name):
            return
        if not node_type or not layer:
            raise ValueError(
                f"Cannot create node '{name}': missing proposed type/layer. "
                f"Set from_node_proposed_type/layer or to_node_proposed_type/layer "
                f"in the pending edge."
            )
        self._repo.add_node(
            name, node_type, layer,
            description=f"Auto-created from AI proposal: {name}",
        )

    def _write_log(
        self,
        pending_id: int,
        action: str,
        reason: str = "",
        modifications: dict | None = None,
    ) -> None:
        mods_json = json.dumps(modifications, ensure_ascii=False) if modifications else "{}"
        self._repo._conn.execute(
            """INSERT INTO causal_review_log (pending_id, action, reason, modifications_json)
               VALUES (?, ?, ?, ?)""",
            (pending_id, action, reason, mods_json),
        )

    def get_review_log(self, pending_id: int | None = None) -> list[CausalReviewLog]:
        """Get review log entries, optionally filtered by pending_id."""
        with self._repo.transaction():
            if pending_id:
                rows = self._repo._conn.execute(
                    "SELECT * FROM causal_review_log WHERE pending_id = ? ORDER BY reviewed_at DESC",
                    (pending_id,),
                ).fetchall()
            else:
                rows = self._repo._conn.execute(
                    "SELECT * FROM causal_review_log ORDER BY reviewed_at DESC"
                ).fetchall()
            return [
                CausalReviewLog(**{k: v for k, v in dict(r).items() if k in CausalReviewLog.model_fields})
                for r in rows
            ]
