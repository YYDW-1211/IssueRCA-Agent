from __future__ import annotations

import asyncio
import json
from importlib.resources import files
from typing import Any

from issue_rca.models import IssueContext


def load_demo_data() -> dict[str, Any]:
    path = files("issue_rca").joinpath("fixtures/demo.json")
    return json.loads(path.read_text(encoding="utf-8"))


class MockGitHubClient:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data or load_demo_data()

    async def check_connection(self) -> str:
        return "mock GitHub fixture ready"

    async def fetch_issue(self, url: str) -> IssueContext:
        issue = dict(self.data["issue"])
        issue["url"] = url
        await asyncio.sleep(0.01)
        return IssueContext.model_validate(issue)

    async def close(self) -> None:
        return None


class MockRepositoryGateway:
    def __init__(
        self,
        data: dict[str, Any] | None = None,
        delay_seconds: float = 0.03,
        fail_method: str | None = None,
    ) -> None:
        self.data = data or load_demo_data()
        self.delay_seconds = delay_seconds
        self.fail_method = fail_method
        self.tool_calls = 0

    async def _step(self, method: str) -> None:
        await asyncio.sleep(self.delay_seconds)
        self.tool_calls += 1
        if self.fail_method == method:
            raise RuntimeError(f"mock failure in {method}")

    async def check_connection(self) -> str:
        await self._step("check_connection")
        return "mock Composio GitHub gateway ready"

    async def search_code(
        self, owner: str, repo: str, query: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        await self._step("search_code")
        return list(self.data["code_search"])[:limit]

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str:
        await self._step("get_file_content")
        return str(self.data["files"].get(path, ""))

    async def get_tree(
        self, owner: str, repo: str, ref: str, recursive: bool = True
    ) -> list[dict[str, Any]]:
        await self._step("get_tree")
        return list(self.data["tree"])

    async def search_issues(
        self, owner: str, repo: str, query: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        await self._step("search_issues")
        return list(self.data["issues"])[:limit]

    async def list_commits(self, owner: str, repo: str, limit: int = 10) -> list[dict[str, Any]]:
        await self._step("list_commits")
        return list(self.data["commits"])[:limit]

    async def compare_commits(self, owner: str, repo: str, base: str, head: str) -> dict[str, Any]:
        await self._step("compare_commits")
        return dict(self.data["comparison"])

    async def get_readme(self, owner: str, repo: str, ref: str | None = None) -> str:
        await self._step("get_readme")
        return str(self.data["files"]["README.md"])
