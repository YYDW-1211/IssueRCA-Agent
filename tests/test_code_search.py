from __future__ import annotations

from pathlib import Path

import pytest

from issue_rca.agents.code_search import CodeSearchAgent
from issue_rca.config import Settings
from issue_rca.integrations.lsp import MultilspySearchProvider
from issue_rca.integrations.mock import load_demo_data
from issue_rca.models import InvestigationPlan, IssueContext


class CodeRepository:
    def __init__(self, path: str = "fastapi/openapi/utils.py") -> None:
        self.path = path

    async def search_code(self, owner: str, repo: str, query: str, limit: int = 5):
        return [{"path": self.path, "html_url": f"https://example.test/{self.path}"}]

    async def get_file_content(self, owner: str, repo: str, path: str, ref: str | None = None):
        return "def get_openapi():\n    return {}\n"


class RecordingLsp:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, list[str]]] = []

    async def document_symbols(self, repository_root: Path, files: list[str]):
        self.calls.append((repository_root, files))
        return [{"path": files[0], "symbols": [{"name": "get_openapi"}]}]


def issue() -> IssueContext:
    return IssueContext.model_validate(load_demo_data()["issue"])


def plan() -> InvestigationPlan:
    return InvestigationPlan(
        issue_summary="summary",
        hypotheses=[],
        search_terms=["get_openapi"],
        target_areas=["openapi"],
        agent_tasks={},
    )


def test_multilspy_resolves_installed_jedi_server_binary() -> None:
    binary = Path(MultilspySearchProvider._jedi_server_binary())
    assert binary.is_file()
    assert binary.name.lower().startswith("jedi-language-server")


def test_empty_local_repo_env_is_disabled() -> None:
    settings = Settings(_env_file=None, local_repo_path="")
    assert settings.local_repo_path is None


@pytest.mark.asyncio
async def test_code_search_rejects_missing_repository_root(tmp_path) -> None:
    missing_root = tmp_path / "missing-repository"
    agent = CodeSearchAgent(
        CodeRepository(),
        llm=None,
        lsp=RecordingLsp(),
        local_repo_path=missing_root,
    )

    with pytest.raises(FileNotFoundError, match="LOCAL_REPO_PATH 不存在"):
        await agent.gather(issue(), plan())


@pytest.mark.asyncio
async def test_code_search_skips_lsp_when_remote_file_is_missing_locally(tmp_path) -> None:
    lsp = RecordingLsp()
    agent = CodeSearchAgent(
        CodeRepository(),
        llm=None,
        lsp=lsp,
        local_repo_path=tmp_path,
    )

    raw, tool_calls = await agent.gather(issue(), plan())

    assert tool_calls == 2
    assert raw["candidate_files"][0]["path"] == "fastapi/openapi/utils.py"
    assert raw["lsp_status"] == "skipped_no_matching_local_files"
    assert raw["lsp_missing_files"] == ["fastapi/openapi/utils.py"]
    assert lsp.calls == []


@pytest.mark.asyncio
async def test_code_search_rejects_path_outside_local_repository(tmp_path) -> None:
    lsp = RecordingLsp()
    agent = CodeSearchAgent(
        CodeRepository("../outside.py"),
        llm=None,
        lsp=lsp,
        local_repo_path=tmp_path,
    )

    raw, _ = await agent.gather(issue(), plan())

    assert raw["lsp_status"] == "skipped_no_matching_local_files"
    assert raw["lsp_missing_files"] == ["../outside.py"]
    assert lsp.calls == []


@pytest.mark.asyncio
async def test_code_search_uses_lsp_for_existing_local_file(tmp_path) -> None:
    local_file = tmp_path / "fastapi" / "openapi" / "utils.py"
    local_file.parent.mkdir(parents=True)
    local_file.write_text("def get_openapi():\n    return {}\n", encoding="utf-8")
    lsp = RecordingLsp()
    agent = CodeSearchAgent(
        CodeRepository(),
        llm=None,
        lsp=lsp,
        local_repo_path=tmp_path,
    )

    raw, _ = await agent.gather(issue(), plan())

    assert raw["lsp_status"] == "used"
    assert raw["lsp_symbols"][0]["symbols"][0]["name"] == "get_openapi"
    assert lsp.calls == [(tmp_path.resolve(), ["fastapi/openapi/utils.py"])]
