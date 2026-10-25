from __future__ import annotations

import pytest

from issue_rca.config import Settings
from issue_rca.graph.workflow import RuntimeServices, run_workflow
from issue_rca.integrations.deepseek import MockLLM
from issue_rca.integrations.mock import MockGitHubClient, MockRepositoryGateway


@pytest.mark.asyncio
async def test_graph_runs_four_agents_in_parallel() -> None:
    settings = Settings(issue_rca_mode="mock", max_candidate_files=3)
    services = RuntimeServices(
        settings=settings,
        github=MockGitHubClient(),
        repository=MockRepositoryGateway(delay_seconds=0.04),
        llm=MockLLM(),
    )
    state = await run_workflow("https://github.com/example/checkout-service/issues/42", services)
    evidences = state["evidences"]
    assert {item.agent for item in evidences} == {
        "code_search",
        "issue_history",
        "git_history",
        "documentation",
    }
    report = state["report"]
    assert report.suspected_locations[0].path == "src/checkout/coupon_service.py"
    subagent_sum = sum(item.elapsed_ms for item in evidences)
    assert report.metrics.total_elapsed_ms < subagent_sum


@pytest.mark.asyncio
async def test_one_agent_failure_is_degraded_not_fatal() -> None:
    settings = Settings(issue_rca_mode="mock")
    services = RuntimeServices(
        settings=settings,
        github=MockGitHubClient(),
        repository=MockRepositoryGateway(fail_method="search_issues"),
        llm=MockLLM(),
    )
    state = await run_workflow("https://github.com/example/checkout-service/issues/42", services)
    history = next(item for item in state["evidences"] if item.agent == "issue_history")
    assert history.status == "error"
    assert state["report"].root_cause
    assert state["report"].metrics.errors
