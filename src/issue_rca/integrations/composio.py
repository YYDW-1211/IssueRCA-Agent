from __future__ import annotations

import asyncio
import base64
from typing import Any


class ComposioIntegrationError(RuntimeError):
    pass


class ComposioSecurityError(ValueError):
    pass


READONLY_GITHUB_TOOLS = (
    "GITHUB_GET_THE_AUTHENTICATED_USER",
    "GITHUB_SEARCH_CODE",
    "GITHUB_GET_RAW_REPOSITORY_CONTENT",
    "GITHUB_GET_A_TREE",
    "GITHUB_SEARCH_ISSUES",
    "GITHUB_LIST_COMMITS",
    "GITHUB_COMPARE_TWO_COMMITS",
    "GITHUB_GET_A_REPOSITORY_README",
)

_BLOCKED_TAGS = ("createHint", "updateHint", "destructiveHint")


def validate_readonly_tool(tool_slug: str) -> None:
    if tool_slug not in READONLY_GITHUB_TOOLS:
        raise ComposioSecurityError(f"Composio tool 不在 GitHub 只读白名单中: {tool_slug}")


def build_session_config(
    user_id: str,
    connected_account_id: str | None = None,
) -> dict[str, Any]:
    """Build a native-tool-only session without HTTP proxy or workbench access."""
    config: dict[str, Any] = {
        "user_id": user_id,
        "toolkits": {"enable": ["github"]},
        "tools": {"github": {"enable": list(READONLY_GITHUB_TOOLS)}},
        "tags": {"disable": list(_BLOCKED_TAGS)},
        "sandbox": {"enable": False, "enable_proxy_execution": False},
        "proxy_execute": {"enable": False},
    }
    if connected_account_id:
        config["connected_accounts"] = {"github": [connected_account_id]}
    return config


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _decode_content(data: Any) -> str:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return str(data or "")
    content = data.get("content") or data.get("text") or ""
    if data.get("encoding") == "base64":
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return ""
    return str(content)


def _connection_error_message(message: str) -> str:
    if "No active connection found" not in message:
        return message
    return (
        "Composio Platform 项目中没有与当前 COMPOSIO_USER_ID 匹配的 ACTIVE GitHub "
        "connected account。GitHub Token 的 doctor PASS 只验证项目自己的 GitHub REST "
        "客户端，不能替代 Composio 连接。请在 Composio Platform 为该 user ID 连接 "
        "GitHub，或设置 COMPOSIO_GITHUB_CONNECTED_ACCOUNT_ID。"
    )


class ComposioGitHubGateway:
    """Read-only GitHub access through explicit Composio native tools."""

    def __init__(
        self,
        api_key: str,
        user_id: str,
        connected_account_id: str | None = None,
    ) -> None:
        try:
            from composio import Composio
        except ImportError as exc:  # pragma: no cover - dependency error path
            raise ComposioIntegrationError("未安装 composio，请执行 pip install -e .") from exc

        if api_key.startswith("ck_"):
            raise ComposioIntegrationError(
                "检测到 Connect consumer key；当前 Platform Sessions SDK "
                "需要 ak_* Project API Key。"
            )

        composio = Composio(api_key=api_key)
        kwargs = build_session_config(user_id, connected_account_id)
        try:
            self._session = composio.sessions.create(**kwargs)
        except TypeError:
            # Compatibility with SDK builds exposing the create() shortcut.
            self._session = composio.create(**kwargs)
        self.tool_calls = 0

    async def _execute(self, tool_slug: str, arguments: dict[str, Any]) -> Any:
        validate_readonly_tool(tool_slug)
        self.tool_calls += 1
        try:
            response = await asyncio.to_thread(
                self._session.execute,
                tool_slug,
                arguments=arguments,
            )
        except Exception as exc:
            message = _connection_error_message(str(exc))
            raise ComposioIntegrationError(message) from exc

        result_type = _value(response, "result_type")
        response_error = _value(response, "error")
        payload = _value(response, "data", {})
        if result_type == "failed" or response_error:
            raise ComposioIntegrationError(
                _connection_error_message(str(response_error or payload or "Composio tool failed"))
            )

        if isinstance(payload, dict) and "successful" in payload:
            if not payload.get("successful"):
                message = payload.get("error") or f"{tool_slug} execution failed"
                raise ComposioIntegrationError(_connection_error_message(str(message)))
            return payload.get("data")
        return payload

    async def check_connection(self) -> str:
        data = await self._execute("GITHUB_GET_THE_AUTHENTICATED_USER", {})
        login = data.get("login", "unknown") if isinstance(data, dict) else "unknown"
        return f"native GitHub tool connected as {login}"

    async def search_code(
        self, owner: str, repo: str, query: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        data = await self._execute(
            "GITHUB_SEARCH_CODE",
            {"q": f"{query} repo:{owner}/{repo}", "per_page": min(limit, 30)},
        )
        return list((data or {}).get("items", []))[:limit]

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str:
        clean_path = path.replace("\\", "/").lstrip("/")
        arguments: dict[str, Any] = {"owner": owner, "repo": repo, "path": clean_path}
        if ref:
            arguments["ref"] = ref
        data = await self._execute("GITHUB_GET_RAW_REPOSITORY_CONTENT", arguments)
        return _decode_content(data)

    async def get_tree(
        self, owner: str, repo: str, ref: str, recursive: bool = True
    ) -> list[dict[str, Any]]:
        data = await self._execute(
            "GITHUB_GET_A_TREE",
            {"owner": owner, "repo": repo, "tree_sha": ref, "recursive": recursive},
        )
        return list((data or {}).get("tree", []))

    async def search_issues(
        self, owner: str, repo: str, query: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        data = await self._execute(
            "GITHUB_SEARCH_ISSUES",
            {"q": f"repo:{owner}/{repo} is:issue {query}", "per_page": min(limit, 30)},
        )
        return list((data or {}).get("items", []))[:limit]

    async def list_commits(self, owner: str, repo: str, limit: int = 10) -> list[dict[str, Any]]:
        data = await self._execute(
            "GITHUB_LIST_COMMITS",
            {"owner": owner, "repo": repo, "per_page": min(limit, 30)},
        )
        if data is None:
            return []
        if isinstance(data, dict):
            if "commits" not in data:
                raise ComposioIntegrationError(
                    f"GITHUB_LIST_COMMITS 返回对象缺少 commits 字段: keys={sorted(data.keys())}"
                )
            commits = data["commits"]
        elif isinstance(data, list):
            commits = data
        else:
            raise ComposioIntegrationError(
                f"GITHUB_LIST_COMMITS 返回类型异常: {type(data).__name__}"
            )
        if not isinstance(commits, list):
            raise ComposioIntegrationError(
                f"GITHUB_LIST_COMMITS.commits 必须是 list，实际为 {type(commits).__name__}"
            )
        for index, commit in enumerate(commits):
            if not isinstance(commit, dict):
                raise ComposioIntegrationError(
                    f"GITHUB_LIST_COMMITS.commits[{index}] 必须是 object，实际为 "
                    f"{type(commit).__name__}"
                )
        return commits[:limit]

    async def compare_commits(self, owner: str, repo: str, base: str, head: str) -> dict[str, Any]:
        data = await self._execute(
            "GITHUB_COMPARE_TWO_COMMITS",
            {"owner": owner, "repo": repo, "basehead": f"{base}...{head}"},
        )
        return dict(data or {})

    async def get_readme(self, owner: str, repo: str, ref: str | None = None) -> str:
        arguments: dict[str, Any] = {"owner": owner, "repo": repo}
        if ref:
            arguments["ref"] = ref
        data = await self._execute("GITHUB_GET_A_REPOSITORY_README", arguments)
        return _decode_content(data)
