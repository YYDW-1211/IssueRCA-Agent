from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from issue_rca.integrations.composio import (
    ComposioIntegrationError,
    build_session_config,
)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass(frozen=True)
class ConnectedAccountSummary:
    id: str
    status: str
    toolkit: str
    user_id: str


def validate_github_connected_account(
    account: Any,
    expected_user_id: str,
) -> ConnectedAccountSummary:
    account_id = str(_value(account, "id", "") or "")
    status = str(_value(account, "status", "") or "")
    user_id = str(_value(account, "user_id", "") or "")
    toolkit_value = _value(account, "toolkit", {})
    toolkit = str(
        _value(toolkit_value, "slug", "")
        or _value(toolkit_value, "name", "")
        or toolkit_value
        or ""
    ).lower()

    problems: list[str] = []
    if not account_id:
        problems.append("connected account ID 缺失")
    if status != "ACTIVE":
        problems.append(f"status={status or 'unknown'}，预期 ACTIVE")
    if toolkit != "github":
        problems.append(f"toolkit={toolkit or 'unknown'}，预期 github")
    if user_id != expected_user_id:
        problems.append(f"user_id={user_id or 'unknown'}，预期 {expected_user_id}")
    if problems:
        raise ComposioIntegrationError("Composio GitHub 连接校验失败：" + "；".join(problems))

    return ConnectedAccountSummary(
        id=account_id,
        status=status,
        toolkit=toolkit,
        user_id=user_id,
    )


class ComposioGitHubAuthorizer:
    """Create and verify a GitHub connected account without exposing credentials."""

    def __init__(self, api_key: str, user_id: str) -> None:
        try:
            from composio import Composio
        except ImportError as exc:  # pragma: no cover - dependency error path
            raise ComposioIntegrationError("未安装 composio，请执行 pip install -e .") from exc

        if api_key.startswith("ck_"):
            raise ComposioIntegrationError(
                "检测到 Connect consumer key；授权流程需要 ak_* Project API Key。"
            )

        self.user_id = user_id
        self._composio = Composio(api_key=api_key)
        config = build_session_config(user_id)
        try:
            self._session = self._composio.sessions.create(**config)
        except TypeError:
            self._session = self._composio.create(**config)

    def authorize(
        self,
        callback_url: str | None = None,
        alias: str | None = None,
    ) -> Any:
        return self._session.authorize(
            "github",
            callback_url=callback_url,
            alias=alias,
        )

    def wait_for_active(self, request: Any, timeout_seconds: float) -> ConnectedAccountSummary:
        account = request.wait_for_connection(timeout_seconds)
        return validate_github_connected_account(account, self.user_id)

    def list_active(self) -> list[ConnectedAccountSummary]:
        response = self._composio.connected_accounts.list(
            toolkit_slugs=["github"],
            statuses=["ACTIVE"],
            user_ids=[self.user_id],
            limit=50,
        )
        items = _value(response, "items", []) or []
        summaries: list[ConnectedAccountSummary] = []
        for item in items:
            summaries.append(validate_github_connected_account(item, self.user_id))
        return summaries


def save_connected_account_id(env_path: Path, connected_account_id: str) -> None:
    """Update only COMPOSIO_GITHUB_CONNECTED_ACCOUNT_ID while preserving .env."""
    if not connected_account_id:
        raise ValueError("connected_account_id 不能为空")

    key = "COMPOSIO_GITHUB_CONNECTED_ACCOUNT_ID"
    original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = original.splitlines()
    replacement = f"{key}={connected_account_id}"
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(f"{key}="):
            if not replaced:
                updated.append(replacement)
                replaced = True
            continue
        updated.append(line)
    if not replaced:
        if updated and updated[-1] != "":
            updated.append("")
        updated.append(replacement)

    output = "\n".join(updated) + "\n"
    temp_path = env_path.with_name(f"{env_path.name}.tmp")
    temp_path.write_text(output, encoding="utf-8")
    temp_path.replace(env_path)
