from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from issue_rca.integrations.composio import (
    READONLY_GITHUB_TOOLS,
    ComposioGitHubGateway,
    ComposioIntegrationError,
    ComposioSecurityError,
    build_session_config,
    validate_readonly_tool,
)


def test_composio_session_uses_native_readonly_tools_only() -> None:
    config = build_session_config("issue-rca-local")

    assert config["toolkits"] == {"enable": ["github"]}
    assert set(config["tools"]["github"]["enable"]) == set(READONLY_GITHUB_TOOLS)
    assert config["tags"] == {"disable": ["createHint", "updateHint", "destructiveHint"]}
    assert config["proxy_execute"] == {"enable": False}
    assert config["sandbox"] == {
        "enable": False,
        "enable_proxy_execution": False,
    }


def test_composio_rejects_tools_outside_allowlist() -> None:
    validate_readonly_tool("GITHUB_SEARCH_CODE")
    validate_readonly_tool("GITHUB_GET_A_REPOSITORY_README")

    with pytest.raises(ComposioSecurityError):
        validate_readonly_tool("GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS")
    with pytest.raises(ComposioSecurityError):
        validate_readonly_tool("COMPOSIO_REMOTE_BASH_TOOL")


class FakeNativeSession:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, tool_slug: str, *, arguments: dict[str, object]):
        self.calls.append((tool_slug, arguments))
        if self.error:
            raise self.error
        return SimpleNamespace(
            result_type="completed",
            error=None,
            data={"successful": True, "data": {"login": "alice"}},
        )

    def proxy_execute(self, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("proxy_execute must not be used")


def gateway_with_session(session: FakeNativeSession) -> ComposioGitHubGateway:
    gateway = object.__new__(ComposioGitHubGateway)
    gateway._session = session
    gateway.tool_calls = 0
    return gateway


async def direct_call(func, *args, **kwargs):
    return func(*args, **kwargs)


@pytest.mark.asyncio
async def test_doctor_connection_check_uses_native_github_tool(monkeypatch) -> None:
    monkeypatch.setattr(asyncio, "to_thread", direct_call)
    session = FakeNativeSession()
    gateway = gateway_with_session(session)

    detail = await gateway.check_connection()

    assert detail == "native GitHub tool connected as alice"
    assert session.calls == [("GITHUB_GET_THE_AUTHENTICATED_USER", {})]
    assert gateway.tool_calls == 1


@pytest.mark.asyncio
async def test_missing_platform_connection_has_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr(asyncio, "to_thread", direct_call)
    session = FakeNativeSession(
        RuntimeError("No active connection found for toolkit(s) 'github' in this session")
    )
    gateway = gateway_with_session(session)

    with pytest.raises(ComposioIntegrationError, match="ACTIVE GitHub connected account"):
        await gateway.check_connection()


def test_secret_values_do_not_appear_in_env_template() -> None:
    content = open(".env.example", encoding="utf-8").read()
    assert "ghp_" not in content
    assert "sk-" not in content
    assert "your_deepseek_key" not in content
