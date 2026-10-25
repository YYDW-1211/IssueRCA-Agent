from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class IssueComment(BaseModel):
    author: str
    body: str
    created_at: datetime | None = None
    url: str | None = None


class IssueContext(BaseModel):
    url: str
    owner: str
    repo: str
    number: int
    title: str
    body: str = ""
    state: str = "open"
    labels: list[str] = Field(default_factory=list)
    comments: list[IssueComment] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    default_branch: str = "main"

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def combined_text(self) -> str:
        comments = "\n".join(comment.body for comment in self.comments[:20])
        return f"{self.title}\n{self.body}\n{comments}".strip()


class BugHypothesis(BaseModel):
    id: str
    statement: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    keywords: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)


class InvestigationPlan(BaseModel):
    issue_summary: str
    hypotheses: list[BugHypothesis]
    search_terms: list[str] = Field(default_factory=list)
    target_areas: list[str] = Field(default_factory=list)
    agent_tasks: dict[str, str] = Field(default_factory=dict)


class SuspectedLocation(BaseModel):
    path: str
    symbol: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    reason: str = Field(max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        return value.replace("\\", "/").lstrip("/")


class EvidenceDraft(BaseModel):
    summary: str
    findings: list[str] = Field(default_factory=list)
    suspected_locations: list[SuspectedLocation] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class AgentEvidence(BaseModel):
    id: str
    agent: Literal["code_search", "issue_history", "git_history", "documentation"]
    status: Literal["success", "degraded", "error"] = "success"
    summary: str
    findings: list[str] = Field(default_factory=list)
    suspected_locations: list[SuspectedLocation] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    elapsed_ms: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    raw_preview: str = ""


class MetricEvent(BaseModel):
    source: str
    elapsed_ms: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


class RunMetrics(BaseModel):
    total_elapsed_ms: int = Field(default=0, ge=0)
    agent_elapsed_ms: dict[str, int] = Field(default_factory=dict)
    model_prompt_tokens: int = Field(default=0, ge=0)
    model_completion_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


class RCADraft(BaseModel):
    executive_summary: str = Field(max_length=1200)
    root_cause: str = Field(max_length=1800)
    confidence: float = Field(ge=0.0, le=1.0)
    suspected_locations: list[SuspectedLocation] = Field(min_length=1, max_length=5)
    supporting_evidence: list[str] = Field(min_length=1, max_length=8)
    contradictory_evidence: list[str] = Field(max_length=3)
    rejected_hypotheses: list[str] = Field(max_length=4)
    verification_steps: list[str] = Field(min_length=1, max_length=6)
    uncertainty: str = Field(max_length=800)


class RCAReport(RCADraft):
    run_id: str
    issue: IssueContext
    metrics: RunMetrics
    generated_at: datetime = Field(default_factory=utc_now)


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMResult(BaseModel):
    data: Any
    usage: LLMUsage = Field(default_factory=LLMUsage)
    raw_text: str = ""


class DoctorCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class RunArtifacts(BaseModel):
    run_dir: str
    report_json: str
    report_markdown: str
    trace_json: str
