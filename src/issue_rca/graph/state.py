from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from issue_rca.models import (
    AgentEvidence,
    InvestigationPlan,
    IssueContext,
    MetricEvent,
    RCAReport,
)


class GraphState(TypedDict, total=False):
    issue_url: str
    run_id: str
    started_at_iso: str
    started_perf: float
    issue: IssueContext
    plan: InvestigationPlan
    evidences: Annotated[list[AgentEvidence], operator.add]
    metric_events: Annotated[list[MetricEvent], operator.add]
    report: RCAReport
