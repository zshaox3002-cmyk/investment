"""CausalRepo: CRUD operations for causal graph nodes and edges."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from investment.core.db import connect, transaction as db_transaction

from .models import CausalNode, CausalEdge, CausalEdgeFull, EdgeScore5D, PendingEdge


class CausalRepo:
    """CRUD access to causal_nodes and causal_edges tables."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path

    @contextmanager
    def transaction(self) -> Iterator["CausalRepo"]:
        """Context manager wrapping a DB transaction. Commits on clean exit."""
        with db_transaction(self._db_path) as conn:
            self._conn = conn
            try:
                yield self
            finally:
                del self._conn

    # ── Node operations ──────────────────────────────────────────────────

    def add_node(
        self,
        name: str,
        node_type: str,
        layer: str,
        description: str = "",
        keywords: str | None = None,
    ) -> int:
        """Insert a node. Returns node_id."""
        kw = keywords or "[]"
        now = _utcnow()
        cur = self._conn.execute(
            """INSERT INTO causal_nodes (name, node_type, layer, description, keywords, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, node_type, layer, description, kw, now),
        )
        return cur.lastrowid

    def get_node(self, name: str) -> CausalNode | None:
        """Look up a node by name."""
        row = self._conn.execute(
            "SELECT * FROM causal_nodes WHERE name = ?", (name,)
        ).fetchone()
        return _dict_to(CausalNode, row) if row else None

    def list_nodes(
        self,
        layer: str | None = None,
        state: str | None = None,
        node_type: str | None = None,
    ) -> list[CausalNode]:
        """List nodes, optionally filtered."""
        sql = "SELECT * FROM causal_nodes WHERE 1=1"
        params: list = []
        if layer:
            sql += " AND layer = ?"
            params.append(layer)
        if state:
            sql += " AND lifecycle_state = ?"
            params.append(state)
        if node_type:
            sql += " AND node_type = ?"
            params.append(node_type)
        sql += " ORDER BY layer, node_type, name"
        rows = self._conn.execute(sql, params).fetchall()
        return [_dict_to(CausalNode, r) for r in rows]

    def update_node_lifecycle(self, name: str, state: str) -> bool:
        """Transition a node to a new lifecycle state. Returns True if updated."""
        cur = self._conn.execute(
            "UPDATE causal_nodes SET lifecycle_state=?, updated_at=? WHERE name=?",
            (state, _utcnow(), name),
        )
        return cur.rowcount > 0

    def update_node_activation(
        self, node_id: int, score: float, signal_at: str | None = None
    ) -> bool:
        """Update activation score and optionally last_signal_at."""
        cur = self._conn.execute(
            "UPDATE causal_nodes SET activation_score=?, last_signal_at=COALESCE(?, last_signal_at), "
            "signal_count_30d = signal_count_30d + 1, updated_at=? WHERE node_id=?",
            (score, signal_at, _utcnow(), node_id),
        )
        return cur.rowcount > 0

    # ── Edge operations ──────────────────────────────────────────────────

    def add_edge(
        self,
        from_name: str,
        to_name: str,
        direction: int,
        d1: int = 3,
        d2: int = 3,
        d3: int = 3,
        d4: int = 3,
        d5: int = 3,
        lag_days: int = 0,
        evidence_summary: str = "",
        evidence_urls: str = "[]",
        approved_by: str = "human",
    ) -> int:
        """Add an edge by node names. Returns edge_id.

        Raises ValueError if either node is not found, or if edge already exists.
        """
        from_node = self.get_node(from_name)
        if not from_node:
            raise ValueError(f"Node not found: {from_name}")
        to_node = self.get_node(to_name)
        if not to_node:
            raise ValueError(f"Node not found: {to_name}")

        scores = EdgeScore5D(
            d1_directness=d1,
            d2_elasticity=d2,
            d3_consistency=d3,
            d4_speed=d4,
            d5_uniqueness=d5,
        )
        strength = scores.composite_strength()
        now = _utcnow()

        try:
            cur = self._conn.execute(
                """INSERT INTO causal_edges
                   (from_node_id, to_node_id, direction,
                    d1_directness, d2_elasticity, d3_consistency, d4_speed, d5_uniqueness,
                    strength, lag_days, evidence_summary, evidence_urls,
                    approved_by, approved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    from_node.node_id, to_node.node_id, direction,
                    d1, d2, d3, d4, d5,
                    strength, lag_days, evidence_summary, evidence_urls,
                    approved_by, now,
                ),
            )
            return cur.lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise ValueError(
                    f"Edge from '{from_name}' to '{to_name}' already exists"
                ) from exc
            raise

    def list_edges(
        self,
        from_name: str | None = None,
        to_name: str | None = None,
    ) -> list[CausalEdgeFull]:
        """List edges via the full view, optionally filtered."""
        sql = "SELECT * FROM v_causal_edges_full WHERE 1=1"
        params: list = []
        if from_name:
            sql += " AND from_name = ?"
            params.append(from_name)
        if to_name:
            sql += " AND to_name = ?"
            params.append(to_name)
        sql += " ORDER BY from_layer, from_name, to_layer, to_name"
        rows = self._conn.execute(sql, params).fetchall()
        return [_dict_to(CausalEdgeFull, r) for r in rows]

    def get_edge(self, edge_id: int) -> CausalEdgeFull | None:
        """Look up a single edge by ID."""
        row = self._conn.execute(
            "SELECT * FROM v_causal_edges_full WHERE edge_id = ?", (edge_id,)
        ).fetchone()
        return _dict_to(CausalEdgeFull, row) if row else None

    def node_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM causal_nodes WHERE name = ?", (name,)
        ).fetchone()
        return row is not None

    # ── Pending edge operations ────────────────────────────────────────────

    def add_pending_edge(
        self,
        from_node_name: str,
        to_node_name: str,
        direction: int,
        from_node_proposed_type: str | None = None,
        from_node_proposed_layer: str | None = None,
        to_node_proposed_type: str | None = None,
        to_node_proposed_layer: str | None = None,
        d1: int | None = None,
        d2: int | None = None,
        d3: int | None = None,
        d4: int | None = None,
        d5: int | None = None,
        lag_days: int = 0,
        confidence: float | None = None,
        evidence_summary: str = "",
        evidence_urls: str = "[]",
        triggered_by_event: str = "",
    ) -> int:
        """Insert a pending edge proposal. Returns pending_id."""
        cur = self._conn.execute(
            """INSERT INTO pending_edges
               (from_node_name, to_node_name, direction,
                from_node_proposed_type, from_node_proposed_layer,
                to_node_proposed_type, to_node_proposed_layer,
                d1_directness, d2_elasticity, d3_consistency, d4_speed, d5_uniqueness,
                lag_days, confidence, evidence_summary, evidence_urls,
                triggered_by_event, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                from_node_name, to_node_name, direction,
                from_node_proposed_type, from_node_proposed_layer,
                to_node_proposed_type, to_node_proposed_layer,
                d1, d2, d3, d4, d5,
                lag_days, confidence, evidence_summary, evidence_urls,
                triggered_by_event,
            ),
        )
        return cur.lastrowid

    def list_pending(self, status: str | None = None) -> list[PendingEdge]:
        """List pending edges, optionally filtered by status."""
        sql = "SELECT * FROM pending_edges WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY confidence DESC, created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [_dict_to(PendingEdge, r) for r in rows]

    def get_pending(self, pending_id: int) -> PendingEdge | None:
        """Look up a single pending edge."""
        row = self._conn.execute(
            "SELECT * FROM pending_edges WHERE pending_id = ?", (pending_id,)
        ).fetchone()
        return _dict_to(PendingEdge, row) if row else None

    def pending_edge_exists(self, from_name: str, to_name: str) -> bool:
        """Check if a pending edge (or approved edge) already exists for this pair."""
        row = self._conn.execute(
            """SELECT 1 FROM pending_edges
               WHERE from_node_name=? AND to_node_name=? AND status='pending'
               UNION
               SELECT 1 FROM causal_edges e
               JOIN causal_nodes n1 ON e.from_node_id = n1.node_id
               JOIN causal_nodes n2 ON e.to_node_id = n2.node_id
               WHERE n1.name=? AND n2.name=?""",
            (from_name, to_name, from_name, to_name),
        ).fetchone()
        return row is not None

    def get_subgraph(self, holding_name: str, hops: int = 2) -> dict:
        """Return the subgraph around a holding node within N hops.

        Returns ``{nodes: list[CausalNode], edges: list[CausalEdgeFull]}``.
        """
        # Collect connected node names via BFS in Python (graph is small)
        visited: set[str] = {holding_name}
        frontier = {holding_name}

        for _ in range(hops):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for name in frontier:
                # Find edges where this node is either source or target
                rows = self._conn.execute(
                    """SELECT from_name, to_name FROM v_causal_edges_full
                       WHERE from_name = ? OR to_name = ?""",
                    (name, name),
                ).fetchall()
                for r in rows:
                    if r["from_name"] not in visited:
                        next_frontier.add(r["from_name"])
                        visited.add(r["from_name"])
                    if r["to_name"] not in visited:
                        next_frontier.add(r["to_name"])
                        visited.add(r["to_name"])
            frontier = next_frontier

        # Fetch all nodes in visited set
        nodes: list[CausalNode] = []
        for name in visited:
            node = self.get_node(name)
            if node:
                nodes.append(node)

        # Fetch all edges between visited nodes
        if not visited:
            return {"nodes": nodes, "edges": []}
        placeholders = ",".join("?" for _ in visited)
        # Parameters doubled: one set for from_name IN, one for to_name IN
        params = list(visited) + list(visited)
        rows = self._conn.execute(
            f"""SELECT * FROM v_causal_edges_full
                WHERE from_name IN ({placeholders})
                  AND to_name IN ({placeholders})""",
            params,
        ).fetchall()
        edges = [_dict_to(CausalEdgeFull, r) for r in rows]

        return {"nodes": nodes, "edges": edges}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dict_to(cls, row) -> object:
    """Convert sqlite3.Row to a Pydantic model instance."""
    d = dict(row)
    # Convert column naming: snake_case fields match column names directly
    return cls(**{k: v for k, v in d.items() if k in cls.model_fields})
