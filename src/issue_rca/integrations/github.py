from __future__ import annotations

import re
from typing import Any

import httpx

from issue_rca.models import IssueComment, IssueContext

ISSUE_URL_PATTERN = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/issues/(?P<number>\d+)(?:[/?#].*)?$"
)


class GitHubAPIError(RuntimeError):
    pass


def parse_issue_url(url: str) -> tuple[str, str, int]:
    match = ISSUE_URL_PATTERN.match(url.strip())
    if not match:
        raise ValueError("Issue URL 必须形如 https://github.com/<owner>/<repo>/issues/<number>")
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def normalize_issue(
    url: str,
    issue_data: dict[str, Any],
    comments_data: list[dict[str, Any]],
    repo_data: dict[str, Any] | None = None,
) -> IssueContext:
    owner, repo, number = parse_issue_url(url)
    labels = [
        item.get("name", "") if isinstance(item, dict) else str(item)
        for item in issue_data.get("labels", [])
    ]
    comments = [
        IssueComment(
            author=(item.get("user") or {}).get("login", "unknown"),
            body=item.get("body") or "",
            created_at=item.get("created_at"),
            url=item.get("html_url"),
        )
        for item in comments_data
    ]
    return IssueContext(
        url=url,
        owner=owner,
        repo=repo,
        number=number,
        title=issue_data.get("title") or f"Issue #{number}",
        body=issue_data.get("body") or "",
        state=issue_data.get("state") or "unknown",
        labels=[label for label in labels if label],
        comments=comments,
        created_at=issue_data.get("created_at"),
        updated_at=issue_data.get("updated_at"),
        default_branch=(repo_data or {}).get("default_branch") or "main",
    )


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_version: str = "2026-03-10",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": api_version,
            "User-Agent": "IssueRCA-Agent/0.1",
        }
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._client.get(path, params=params)
        if response.status_code >= 400:
            message = response.text[:500]
            raise GitHubAPIError(f"GitHub API {response.status_code}: {message}")
        return response.json()

    async def check_connection(self) -> str:
        data = await self._get("/user")
        return f"authenticated as {data.get('login', 'unknown')}"

    async def fetch_issue(self, url: str) -> IssueContext:
        owner, repo, number = parse_issue_url(url)
        issue_data = await self._get(f"/repos/{owner}/{repo}/issues/{number}")
        comments_data = await self._get(
            f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": 100}
        )
        repo_data = await self._get(f"/repos/{owner}/{repo}")
        return normalize_issue(url, issue_data, comments_data, repo_data)
