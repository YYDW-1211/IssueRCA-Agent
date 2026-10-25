from __future__ import annotations

import pytest

from issue_rca.agents.git_history import GitHistoryAgent
from issue_rca.integrations.composio import ComposioGitHubGateway, ComposioIntegrationError
from issue_rca.integrations.mock import load_demo_data
from issue_rca.models import InvestigationPlan, IssueContext


def commit(sha: str, message: str = "message") -> dict:
    return {
        "sha": sha,
        "html_url": f"https://example.test/commit/{sha}",
        "commit": {"message": message, "author": {"date": "2026-01-01T00:00:00Z"}},
    }


def issue() -> IssueContext:
    return IssueContext.model_validate(load_demo_data()["issue"])


def plan() -> InvestigationPlan:
    return InvestigationPlan(
        issue_summary="summary",
        hypotheses=[],
        search_terms=["requestBody"],
        target_areas=["git"],
        agent_tasks={},
    )


async def gateway_with_data(data):
    gateway = object.__new__(ComposioGitHubGateway)

    async def execute(tool_slug, arguments):
        assert tool_slug == "GITHUB_LIST_COMMITS"
        return data

    gateway._execute = execute
    return gateway


@pytest.mark.asyncio
async def test_composio_list_commits_unwraps_commits_object() -> None:
    gateway = await gateway_with_data({"commits": [commit("a"), commit("b")]})

    commits = await gateway.list_commits("fastapi", "fastapi", limit=10)

    assert [item["sha"] for item in commits] == ["a", "b"]


@pytest.mark.asyncio
async def test_composio_list_commits_accepts_empty_result() -> None:
    gateway = await gateway_with_data({"commits": []})
    assert await gateway.list_commits("fastapi", "fastapi") == []


@pytest.mark.asyncio
async def test_composio_list_commits_rejects_malformed_items() -> None:
    gateway = await gateway_with_data({"commits": ["not-an-object"]})

    with pytest.raises(ComposioIntegrationError, match=r"commits\[0\]"):
        await gateway.list_commits("fastapi", "fastapi")


class GitRepository:
    def __init__(self, commits, comparison=None) -> None:
        self.commits = commits
        self.comparison = comparison or {"status": "ahead", "files": []}
        self.compare_calls = 0

    async def list_commits(self, owner: str, repo: str, limit: int = 10):
        return self.commits

    async def compare_commits(self, owner: str, repo: str, base: str, head: str):
        self.compare_calls += 1
        return self.comparison


@pytest.mark.asyncio
async def test_git_history_normal_result() -> None:
    repository = GitRepository(
        [commit("new", "new commit"), commit("old", "old commit")],
        {
            "status": "ahead",
            "ahead_by": 1,
            "files": [{"filename": "fastapi/openapi/utils.py", "status": "modified"}],
        },
    )
    agent = GitHistoryAgent(repository, llm=None)

    raw, tool_calls = await agent.gather(issue(), plan())

    assert tool_calls == 2
    assert raw["recent_commits"][0]["sha"] == "new"
    assert raw["latest_commit_comparison"]["files"][0]["filename"] == ("fastapi/openapi/utils.py")


@pytest.mark.asyncio
async def test_git_history_empty_result_does_not_invent_commits() -> None:
    repository = GitRepository([])
    agent = GitHistoryAgent(repository, llm=None)

    raw, tool_calls = await agent.gather(issue(), plan())

    assert tool_calls == 1
    assert raw["recent_commits"] == []
    assert raw["latest_commit_comparison"] == {}
    assert repository.compare_calls == 0


@pytest.mark.asyncio
async def test_git_history_rejects_unexpected_commit_type() -> None:
    repository = GitRepository(["not-an-object"])
    agent = GitHistoryAgent(repository, llm=None)

    with pytest.raises(TypeError, match="必须是 object"):
        await agent.gather(issue(), plan())
