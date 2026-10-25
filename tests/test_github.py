from __future__ import annotations

import httpx
import pytest

from issue_rca.integrations.github import GitHubClient, normalize_issue, parse_issue_url


def test_parse_issue_url() -> None:
    assert parse_issue_url("https://github.com/openai/example/issues/123") == (
        "openai",
        "example",
        123,
    )
    with pytest.raises(ValueError):
        parse_issue_url("https://github.com/openai/example/pull/123")


def test_normalize_issue() -> None:
    context = normalize_issue(
        "https://github.com/acme/service/issues/9",
        {
            "title": "boom",
            "body": None,
            "state": "open",
            "labels": [{"name": "bug"}],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        },
        [{"user": {"login": "alice"}, "body": "trace", "created_at": "2026-01-01T01:00:00Z"}],
        {"default_branch": "develop"},
    )
    assert context.repository == "acme/service"
    assert context.labels == ["bug"]
    assert context.comments[0].author == "alice"
    assert context.default_branch == "develop"


@pytest.mark.asyncio
async def test_fetch_issue_with_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/service/issues/9":
            return httpx.Response(
                200, json={"title": "boom", "body": "details", "state": "open", "labels": []}
            )
        if request.url.path == "/repos/acme/service/issues/9/comments":
            return httpx.Response(200, json=[])
        if request.url.path == "/repos/acme/service":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404, json={"message": "not found"})

    client = GitHubClient("token", transport=httpx.MockTransport(handler))
    try:
        issue = await client.fetch_issue("https://github.com/acme/service/issues/9")
    finally:
        await client.close()
    assert issue.title == "boom"
    assert issue.default_branch == "main"
