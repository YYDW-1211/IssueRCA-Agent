from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from issue_rca.cli import app
from issue_rca.config import Settings
from issue_rca.integrations.composio import ComposioIntegrationError
from issue_rca.integrations.composio_auth import (
    ConnectedAccountSummary,
    save_connected_account_id,
    validate_github_connected_account,
)

runner = CliRunner()


def test_validate_active_github_account() -> None:
    account = SimpleNamespace(
        id="ca_example",
        status="ACTIVE",
        toolkit=SimpleNamespace(slug="github"),
        user_id="issue-rca-local",
    )

    summary = validate_github_connected_account(account, "issue-rca-local")

    assert summary == ConnectedAccountSummary(
        id="ca_example",
        status="ACTIVE",
        toolkit="github",
        user_id="issue-rca-local",
    )


@pytest.mark.parametrize(
    ("status", "toolkit", "user_id"),
    [
        ("INITIATED", "github", "issue-rca-local"),
        ("ACTIVE", "gmail", "issue-rca-local"),
        ("ACTIVE", "github", "another-user"),
    ],
)
def test_validate_rejects_wrong_connection_identity(
    status: str,
    toolkit: str,
    user_id: str,
) -> None:
    account = SimpleNamespace(
        id="ca_example",
        status=status,
        toolkit=SimpleNamespace(slug=toolkit),
        user_id=user_id,
    )

    with pytest.raises(ComposioIntegrationError):
        validate_github_connected_account(account, "issue-rca-local")


def test_save_connected_account_id_preserves_other_env_values(tmp_path) -> None:
    env_path = tmp_path / ".env"
    original = (
        "COMPOSIO_API_KEY=ak_do_not_change\n"
        "GITHUB_TOKEN=do_not_change\n"
        "COMPOSIO_GITHUB_CONNECTED_ACCOUNT_ID=old_id\n"
        "DEEPSEEK_API_KEY=do_not_change\n"
    )
    env_path.write_text(original, encoding="utf-8")

    save_connected_account_id(env_path, "ca_new")

    updated = env_path.read_text(encoding="utf-8")
    assert "COMPOSIO_GITHUB_CONNECTED_ACCOUNT_ID=ca_new" in updated
    assert "old_id" not in updated
    assert "COMPOSIO_API_KEY=ak_do_not_change" in updated
    assert "GITHUB_TOKEN=do_not_change" in updated
    assert "DEEPSEEK_API_KEY=do_not_change" in updated


class FakeAuthorizationRequest:
    redirect_url = "https://connect.composio.dev/link/test-request"


class FakeAuthorizer:
    def __init__(self, api_key: str, user_id: str) -> None:
        assert api_key == "ak_test"
        assert user_id == "issue-rca-local"

    def authorize(self, callback_url=None, alias=None):
        assert callback_url is None
        assert alias is None
        return FakeAuthorizationRequest()

    def wait_for_active(self, request, timeout_seconds):
        assert isinstance(request, FakeAuthorizationRequest)
        assert timeout_seconds == 10
        return ConnectedAccountSummary(
            id="ca_example",
            status="ACTIVE",
            toolkit="github",
            user_id="issue-rca-local",
        )

    def list_active(self):
        return [
            ConnectedAccountSummary(
                id="ca_example",
                status="ACTIVE",
                toolkit="github",
                user_id="issue-rca-local",
            )
        ]


def fake_settings() -> Settings:
    return Settings(
        _env_file=None,
        composio_api_key="ak_test",
        composio_user_id="issue-rca-local",
    )


def test_composio_connect_github_prints_link_without_claiming_success(monkeypatch) -> None:
    monkeypatch.setattr("issue_rca.cli.load_settings", fake_settings)
    monkeypatch.setattr("issue_rca.cli.ComposioGitHubAuthorizer", FakeAuthorizer)

    result = runner.invoke(app, ["composio", "connect", "github", "--no-wait"])

    assert result.exit_code == 0
    assert FakeAuthorizationRequest.redirect_url in result.stdout
    assert "尚未确认授权成功" in result.stdout
    assert "composio status github" in result.stdout
    assert "授权成功并已验证" not in result.stdout


class FakeGateway:
    def __init__(self, api_key: str, user_id: str, connected_account_id: str) -> None:
        assert api_key == "ak_test"
        assert user_id == "issue-rca-local"
        assert connected_account_id == "ca_example"

    async def check_connection(self) -> str:
        return "native GitHub tool connected as alice"


def test_composio_connect_claims_success_only_after_full_verification(monkeypatch) -> None:
    monkeypatch.setattr("issue_rca.cli.load_settings", fake_settings)
    monkeypatch.setattr("issue_rca.cli.ComposioGitHubAuthorizer", FakeAuthorizer)
    monkeypatch.setattr("issue_rca.cli.ComposioGitHubGateway", FakeGateway)

    result = runner.invoke(
        app,
        ["composio", "connect", "github", "--wait-seconds", "10"],
    )

    assert result.exit_code == 0
    assert "授权成功并已验证" in result.stdout
    assert "Status: ACTIVE" in result.stdout
    assert "Toolkit: github" in result.stdout
    assert "User ID: issue-rca-local" in result.stdout
    assert "native GitHub tool connected as alice" in result.stdout


def test_composio_status_lists_active_connection(monkeypatch) -> None:
    monkeypatch.setattr("issue_rca.cli.load_settings", fake_settings)
    monkeypatch.setattr("issue_rca.cli.ComposioGitHubAuthorizer", FakeAuthorizer)

    result = runner.invoke(app, ["composio", "status", "github"])

    assert result.exit_code == 0
    assert "ca_example" in result.stdout
    assert "ACTIVE" in result.stdout
    assert "issue-rca-local" in result.stdout
