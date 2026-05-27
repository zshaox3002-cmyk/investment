"""Causal graph models (Pydantic v2)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class EdgeScore5D(BaseModel):
    """Five-dimensional edge scoring for causal link quality assessment."""

    d1_directness: int = Field(..., ge=1, le=5, description="How direct is the causal link?")
    d2_elasticity: int = Field(..., ge=1, le=5, description="How much does B move per unit A?")
    d3_consistency: int = Field(..., ge=1, le=5, description="How consistently does A→B hold?")
    d4_speed: int = Field(..., ge=1, le=5, description="How fast does the effect propagate?")
    d5_uniqueness: int = Field(..., ge=1, le=5, description="Is A the primary driver of B?")

    def composite_strength(self) -> float:
        """Weighted composite: d1*0.30 + d2*0.25 + d3*0.25 + d4*0.10 + d5*0.10."""
        return (
            self.d1_directness * 0.30
            + self.d2_elasticity * 0.25
            + self.d3_consistency * 0.25
            + self.d4_speed * 0.10
            + self.d5_uniqueness * 0.10
        )


class CausalNode(BaseModel):
    """A node in the causal graph."""

    node_id: Optional[int] = None
    name: str
    node_type: str = Field(..., pattern=r"^(event|macro|commodity|sector|holding|policy)$")
    layer: str = Field(..., pattern=r"^(L0_geopolitical|L1_macro|L2_industry|L3_holding)$")
    description: str = ""
    keywords: str = "[]"
    activation_score: float = 0.0
    last_signal_at: Optional[str] = None
    signal_count_30d: int = 0
    lifecycle_state: str = "active"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CausalEdge(BaseModel):
    """A directed, scored edge between two causal nodes."""

    edge_id: Optional[int] = None
    from_node_id: int
    to_node_id: int
    direction: int = Field(..., ge=-1, le=1, description="1=positive, -1=negative")
    d1_directness: Optional[int] = Field(None, ge=1, le=5)
    d2_elasticity: Optional[int] = Field(None, ge=1, le=5)
    d3_consistency: Optional[int] = Field(None, ge=1, le=5)
    d4_speed: Optional[int] = Field(None, ge=1, le=5)
    d5_uniqueness: Optional[int] = Field(None, ge=1, le=5)
    strength: Optional[float] = None
    lag_days: int = 0
    evidence_summary: str = ""
    evidence_urls: str = "[]"
    approved_by: str = "human"
    approved_at: Optional[str] = None
    created_at: Optional[str] = None

    @model_validator(mode="after")
    def compute_strength(self) -> "CausalEdge":
        scores = EdgeScore5D(
            d1_directness=self.d1_directness or 3,
            d2_elasticity=self.d2_elasticity or 3,
            d3_consistency=self.d3_consistency or 3,
            d4_speed=self.d4_speed or 3,
            d5_uniqueness=self.d5_uniqueness or 3,
        )
        self.strength = scores.composite_strength()
        return self


class CausalEdgeFull(BaseModel):
    """Denormalized edge view (v_causal_edges_full)."""

    edge_id: int
    from_name: str
    from_layer: str
    to_name: str
    to_layer: str
    direction: int
    strength: Optional[float] = None
    lag_days: int = 0
    d1_directness: Optional[int] = None
    d2_elasticity: Optional[int] = None
    d3_consistency: Optional[int] = None
    d4_speed: Optional[int] = None
    d5_uniqueness: Optional[int] = None
    evidence_summary: str = ""


# ── Phase 2: AI discoverer models ────────────────────────────────────────

class ProposedNode(BaseModel):
    """A node within an AI-proposed causal path."""

    name: str
    node_type: str = Field(..., pattern=r"^(event|macro|commodity|sector|holding|policy)$")
    layer: str = Field(..., pattern=r"^(L0_geopolitical|L1_macro|L2_industry|L3_holding)$")
    description: str = ""
    is_new: bool = True


class ProposedEdgeInPath(BaseModel):
    """An edge within an AI-proposed causal path."""

    from_node_name: str
    to_node_name: str
    direction: int = Field(..., ge=-1, le=1)
    d1_directness: int = Field(..., ge=1, le=5)
    d2_elasticity: int = Field(..., ge=1, le=5)
    d3_consistency: int = Field(..., ge=1, le=5)
    d4_speed: int = Field(..., ge=1, le=5)
    d5_uniqueness: int = Field(..., ge=1, le=5)
    lag_days: int = 0
    confidence: float = Field(..., ge=0, le=1)
    evidence_summary: str = ""
    evidence_urls: str = "[]"


class ProposedPath(BaseModel):
    """A single causal transmission path (nodes + edges)."""

    nodes: list[ProposedNode] = []
    edges: list[ProposedEdgeInPath] = []
    narrative: str = ""


class DiscovererOutput(BaseModel):
    """Top-level LLM output schema for causal-discoverer."""

    paths: list[ProposedPath] = []


class PendingEdge(BaseModel):
    """A row in the pending_edges table."""

    pending_id: Optional[int] = None
    from_node_name: str
    to_node_name: str
    from_node_proposed_type: Optional[str] = None
    from_node_proposed_layer: Optional[str] = None
    to_node_proposed_type: Optional[str] = None
    to_node_proposed_layer: Optional[str] = None
    direction: int = Field(..., ge=-1, le=1)
    d1_directness: Optional[int] = Field(None, ge=1, le=5)
    d2_elasticity: Optional[int] = Field(None, ge=1, le=5)
    d3_consistency: Optional[int] = Field(None, ge=1, le=5)
    d4_speed: Optional[int] = Field(None, ge=1, le=5)
    d5_uniqueness: Optional[int] = Field(None, ge=1, le=5)
    lag_days: int = 0
    confidence: Optional[float] = Field(None, ge=0, le=1)
    evidence_summary: str = ""
    evidence_urls: str = "[]"
    triggered_by_event: str = ""
    status: str = "pending"
    created_at: Optional[str] = None
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None


class CausalReviewLog(BaseModel):
    """A row in the causal_review_log table."""

    log_id: Optional[int] = None
    pending_id: Optional[int] = None
    action: str = Field(..., pattern=r"^(approve|reject|modify)$")
    reason: str = ""
    modifications_json: str = "{}"
    reviewed_at: Optional[str] = None


# ── Phase 4: News signals and activation tracking ────────────────────────

class RawNews(BaseModel):
    """A raw news item fetched from a source, before LLM classification."""

    title: str
    content: str = ""
    url: str = ""
    source: str = ""
    date: str = ""
    content_hash: str = ""


class NewsSignal(BaseModel):
    """A row in the news_signals table."""

    signal_id: Optional[int] = None
    date: str
    source: str
    url: str = ""
    title: str
    summary: str = ""
    affected_node_ids: str = "[]"
    signal_strength: float = Field(..., ge=-1, le=1)
    confidence: float = Field(..., ge=0, le=1)
    raw_content_hash: str = ""
    created_at: Optional[str] = None


class NodeActivationLog(BaseModel):
    """A row in the node_activation_log table."""

    log_id: Optional[int] = None
    node_id: Optional[int] = None
    date: str
    delta: float = 0.0
    new_score: float = 0.0
    reason: str = ""
    created_at: Optional[str] = None


class SignalImpactItem(BaseModel):
    """LLM output: one news article's impact on causal nodes."""

    title: str
    affected_nodes: list[str] = []       # list of node names
    signal_strength: float = Field(..., ge=-1, le=1)
    confidence: float = Field(..., ge=0, le=1)
    summary: str = ""


class SignalImpactOutput(BaseModel):
    """Top-level LLM output schema for signal-impact-analyzer."""

    articles: list[SignalImpactItem] = []


# ── Phase 5: Chain assessments ────────────────────────────────────────────

class ChainAssessment(BaseModel):
    """A row in the chain_assessments table."""

    assessment_id: Optional[int] = None
    date: str
    holding_code: str
    impact_score: Optional[float] = None
    impact_level: Optional[str] = None
    direction: Optional[str] = None
    paths_json: str = "[]"
    triggering_signal_ids: str = "[]"
    narrative_md: str = ""
    created_at: Optional[str] = None


class AssessedPath(BaseModel):
    """One triggered path with its computed impact."""

    node_sequence: list[str] = []
    edge_strengths: list[float] = []
    direction: int = 1
    cumulative_decay: float = 1.0
    impact_contribution: float = 0.0


class AssessmentOutput(BaseModel):
    """Top-level LLM output schema for assessment-narrator."""

    narrative_md: str
    direction: str = "neutral"  # positive|negative|neutral
    impact_level: str = "L1"    # L1-L5
    key_nodes: list[str] = []
    suggested_action: str = "建议观察"
