"""Path search engine for causal impact assessment.

Finds all paths between signal-affected nodes and holding nodes through the
causal graph, then computes per-path impact scores and aggregates them.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PathEdge:
    """A single edge along a causal path."""
    from_name: str
    to_name: str
    strength: float
    direction: int  # 1 or -1


@dataclass
class CausalPath:
    """A complete path from signal source to holding target."""
    edges: list[PathEdge] = field(default_factory=list)

    @property
    def node_sequence(self) -> list[str]:
        if not self.edges:
            return []
        seq = [self.edges[0].from_name]
        for e in self.edges:
            seq.append(e.to_name)
        return seq

    @property
    def length(self) -> int:
        return len(self.edges)


def find_paths(
    repo,
    from_node_id: int,
    to_node_id: int,
    max_hops: int = 6,
) -> list[CausalPath]:
    """Find all simple paths from ``from_node_id`` to ``to_node_id``.

    Uses DFS with cycle detection. Only traverses through *active* nodes.
    Forward-direction only (follows edge direction).

    Returns a list of CausalPath objects.
    """
    paths: list[CausalPath] = []

    # Fetch all outgoing edges keyed by from_node_id
    rows = repo._conn.execute(
        """SELECT e.edge_id, n1.name AS from_name, n2.name AS to_name,
                  e.from_node_id, e.to_node_id,
                  e.strength, e.direction,
                  n2.lifecycle_state AS to_state
           FROM causal_edges e
           JOIN causal_nodes n1 ON e.from_node_id = n1.node_id
           JOIN causal_nodes n2 ON e.to_node_id = n2.node_id
           WHERE n1.lifecycle_state = 'active'""",
    ).fetchall()

    # Build adjacency list: from_node_id → list of (to_node_id, PathEdge)
    adjacency: dict[int, list[tuple[int, PathEdge]]] = {}
    for r in rows:
        if r["to_state"] != "active":
            continue
        src = r["from_node_id"]
        edge = PathEdge(
            from_name=r["from_name"],
            to_name=r["to_name"],
            strength=r["strength"] or 3.0,
            direction=r["direction"],
        )
        adjacency.setdefault(src, []).append((r["to_node_id"], edge))

    if from_node_id not in adjacency:
        return paths

    # DFS with visited set for cycle detection
    def dfs(current: int, visited: set[int], current_path: CausalPath):
        if len(current_path.edges) >= max_hops:
            return
        for next_id, edge in adjacency.get(current, []):
            if next_id in visited:
                continue
            new_path = CausalPath(edges=list(current_path.edges) + [edge])
            if next_id == to_node_id:
                paths.append(new_path)
            else:
                visited.add(next_id)
                dfs(next_id, visited, new_path)
                visited.discard(next_id)

    dfs(from_node_id, {from_node_id}, CausalPath())
    return paths


def compute_path_impact(path: CausalPath, signal_strength: float, alpha: float = 0.85) -> float:
    """Compute the impact of a signal propagating along a single causal path.

    Formula::

        impact = signal_strength * PRODUCT(strength_i / 5) * sign(direction_i)
               * alpha^(len - 1)

    where ``alpha`` is the per-hop decay factor (default 0.85).
    """
    if not path.edges:
        return 0.0

    impact = signal_strength
    for edge in path.edges:
        normalized_strength = edge.strength / 5.0
        impact *= normalized_strength * edge.direction

    decay = alpha ** (path.length - 1)
    return impact * decay


@dataclass
class PathImpact:
    """A path paired with its computed impact."""
    path: CausalPath
    impact: float


def aggregate_multi_paths(path_impacts: list[PathImpact]) -> float:
    """Aggregate multiple path impacts into a single score.

    Same-direction impacts add, opposite-direction impacts cancel.
    Returns the net sum of all path impacts.
    """
    total = 0.0
    for pi in path_impacts:
        total += pi.impact
    return total


def grade_impact(impact_score: float) -> tuple[str, str]:
    """Map an aggregated impact score to an L1-L5 grade and direction label.

    Thresholds::

        |impact_score| < 0.05  → L1 (negligible)
        0.05 ≤ |is| < 0.15   → L2 (weak)
        0.15 ≤ |is| < 0.30   → L3 (moderate, actionable)
        0.30 ≤ |is| < 0.50   → L4 (significant)
        |impact_score| ≥ 0.50 → L5 (severe)

    Returns ``(impact_level, direction)``.
    """
    abs_score = abs(impact_score)

    if abs_score >= 0.50:
        level = "L5"
    elif abs_score >= 0.30:
        level = "L4"
    elif abs_score >= 0.15:
        level = "L3"
    elif abs_score >= 0.05:
        level = "L2"
    else:
        level = "L1"

    if impact_score > 0.01:
        direction = "positive"
    elif impact_score < -0.01:
        direction = "negative"
    else:
        direction = "neutral"

    return level, direction
