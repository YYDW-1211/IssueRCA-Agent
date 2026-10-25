from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from issue_rca.config import Settings
from issue_rca.graph.workflow import RuntimeServices, run_workflow
from issue_rca.integrations.composio import ComposioGitHubGateway
from issue_rca.integrations.deepseek import DeepSeekLLM, MockLLM
from issue_rca.integrations.github import GitHubClient
from issue_rca.integrations.mock import MockGitHubClient, MockRepositoryGateway
from issue_rca.models import RCAReport, RunArtifacts
from issue_rca.reporting import write_run_artifacts


@dataclass
class AnalysisResult:
    report: RCAReport
    artifacts: RunArtifacts


def create_services(settings: Settings, mode: Literal["mock", "live"]) -> RuntimeServices:
    if mode == "mock":
        return RuntimeServices(
            settings=settings,
            github=MockGitHubClient(),
            repository=MockRepositoryGateway(),
            llm=MockLLM(),
        )

    settings.validate_live()
    github = GitHubClient(
        token=settings.secret("github_token") or "",
        api_version=settings.github_api_version,
        timeout=settings.request_timeout_seconds,
    )
    repository = ComposioGitHubGateway(
        api_key=settings.secret("composio_api_key") or "",
        user_id=settings.composio_user_id,
        connected_account_id=settings.composio_github_connected_account_id,
    )
    llm = DeepSeekLLM(
        api_key=settings.secret("deepseek_api_key") or "",
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        max_tokens=settings.max_agent_output_tokens,
        synthesis_max_tokens=settings.deepseek_synthesis_max_tokens,
        diagnostics_dir=settings.runs_dir / "_llm_diagnostics",
    )
    return RuntimeServices(settings=settings, github=github, repository=repository, llm=llm)


async def analyze_issue(
    settings: Settings,
    issue_url: str,
    mode: Literal["mock", "live"],
    output_dir: Path | None = None,
) -> AnalysisResult:
    services = create_services(settings, mode)
    try:
        state = await run_workflow(issue_url, services)
        report = RCAReport.model_validate(state["report"])
        artifacts = write_run_artifacts(
            report=report,
            plan=state["plan"],
            evidences=state.get("evidences", []),
            base_dir=output_dir or settings.runs_dir,
        )
        return AnalysisResult(report=report, artifacts=artifacts)
    finally:
        close = getattr(services.github, "close", None)
        if close:
            await close()
