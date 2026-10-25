from __future__ import annotations

from issue_rca.integrations.mock import load_demo_data
from issue_rca.models import (
    IssueContext,
    RCAReport,
    RunMetrics,
    SuspectedLocation,
)
from issue_rca.reporting import render_markdown


def test_markdown_contains_same_top_location_and_root_cause() -> None:
    issue = IssueContext.model_validate(load_demo_data()["issue"])
    report = RCAReport(
        run_id="test-run",
        issue=issue,
        executive_summary="summary",
        root_cause="missing None guard",
        confidence=0.9,
        suspected_locations=[
            SuspectedLocation(
                path="src/checkout/coupon_service.py",
                symbol="apply_coupon",
                reason="dereference",
                confidence=0.9,
            )
        ],
        supporting_evidence=["cache miss"],
        contradictory_evidence=[],
        rejected_hypotheses=[],
        verification_steps=["add regression test"],
        uncertainty="needs test",
        metrics=RunMetrics(total_elapsed_ms=100),
    )
    markdown = render_markdown(report)
    assert report.root_cause in markdown
    assert report.suspected_locations[0].path in markdown
    assert "test-run" in markdown
