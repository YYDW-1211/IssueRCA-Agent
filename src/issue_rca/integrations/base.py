from __future__ import annotations

from typing import Any, Protocol


class RepositoryGateway(Protocol):
    async def search_code(
        self, owner: str, repo: str, query: str, limit: int = 5
    ) -> list[dict[str, Any]]: ...

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str: ...

    async def get_tree(
        self, owner: str, repo: str, ref: str, recursive: bool = True
    ) -> list[dict[str, Any]]: ...

    async def search_issues(
        self, owner: str, repo: str, query: str, limit: int = 5
    ) -> list[dict[str, Any]]: ...

    async def list_commits(
        self, owner: str, repo: str, limit: int = 10
    ) -> list[dict[str, Any]]: ...

    async def compare_commits(
        self, owner: str, repo: str, base: str, head: str
    ) -> dict[str, Any]: ...

    async def get_readme(self, owner: str, repo: str, ref: str | None = None) -> str: ...

    async def check_connection(self) -> str: ...
