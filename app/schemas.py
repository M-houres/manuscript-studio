from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ReviewIssue:
    category: str
    severity: str
    paragraph_index: int
    excerpt: str
    finding: str
    rationale: str
    recommendation: str
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReviewDimensionScore:
    name: str
    score: int
    rationale: str


@dataclass(slots=True)
class ReviewReport:
    title: str
    summary: str
    char_count: int
    overall_score: int
    readiness: str
    strengths: list[str]
    priorities: list[str]
    dimension_scores: list[ReviewDimensionScore]
    issues: list[ReviewIssue]
    revised_outline: list[str]
    provider_name: str = "heuristic"
    model_alias: str = "review_deep"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DiffBlock:
    change_type: str
    original: str
    updated: str
    explanation: str


@dataclass(slots=True)
class RewriteResult:
    title: str
    mode: str
    char_count: int
    summary: str
    optimized_text: str
    strategy_notes: list[str]
    citation_prompts: list[str]
    diff_blocks: list[DiffBlock]
    provider_name: str = "heuristic"
    model_alias: str = "rewrite_quality"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PaymentCreationResult:
    provider: str
    channel: str
    redirect_url: str | None
    qr_code: str | None
    provider_order_id: str | None
    raw_payload: dict[str, Any]
