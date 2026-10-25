from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    issue_rca_mode: Literal["mock", "live"] = "mock"

    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    github_token: SecretStr | None = None
    github_api_version: str = "2026-03-10"

    composio_api_key: SecretStr | None = None
    composio_user_id: str = "issue-rca-local"
    composio_github_connected_account_id: str | None = None

    log_level: str = "INFO"
    runs_dir: Path = Path("artifacts/runs")
    max_candidate_files: int = Field(default=10, ge=1, le=50)
    max_agent_output_tokens: int = Field(default=1200, ge=200, le=8000)
    deepseek_synthesis_max_tokens: int = Field(default=1800, ge=800, le=8000)
    request_timeout_seconds: float = Field(default=30.0, gt=1, le=180)
    local_repo_path: Path | None = None

    @field_validator("local_repo_path", mode="before")
    @classmethod
    def empty_local_repo_path_is_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def secret(self, name: str) -> str | None:
        value = getattr(self, name)
        if value is None:
            return None
        if isinstance(value, SecretStr):
            raw = value.get_secret_value().strip()
            return raw or None
        raw = str(value).strip()
        return raw or None

    def missing_live_settings(self) -> list[str]:
        required = {
            "DEEPSEEK_API_KEY": self.secret("deepseek_api_key"),
            "GITHUB_TOKEN": self.secret("github_token"),
            "COMPOSIO_API_KEY": self.secret("composio_api_key"),
        }
        return [name for name, value in required.items() if not value]

    def validate_live(self) -> None:
        missing = self.missing_live_settings()
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"live 模式缺少必需环境变量: {joined}")


def load_settings(**overrides: object) -> Settings:
    return Settings(**overrides)
