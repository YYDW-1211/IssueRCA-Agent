from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Literal

from issue_rca.integrations.base import RepositoryGateway
from issue_rca.integrations.deepseek import LLMProvider
from issue_rca.models import AgentEvidence, EvidenceDraft, InvestigationPlan, IssueContext

AgentName = Literal["code_search", "issue_history", "git_history", "documentation"]


class BaseEvidenceAgent(ABC):
    name: AgentName

    def __init__(self, repository: RepositoryGateway, llm: LLMProvider) -> None:
        self.repository = repository
        self.llm = llm

    @abstractmethod
    async def gather(
        self, issue: IssueContext, plan: InvestigationPlan
    ) -> tuple[dict[str, Any], int]:
        raise NotImplementedError

    async def run(self, issue: IssueContext, plan: InvestigationPlan) -> AgentEvidence:
        started = time.perf_counter()
        try:
            raw, tool_calls = await self.gather(issue, plan)
            result = await self.llm.summarize_evidence(self.name, issue, plan, raw)
            draft = EvidenceDraft.model_validate(result.data)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return AgentEvidence(
                id=f"E-{self.name}",
                agent=self.name,
                status="success" if draft.findings or draft.suspected_locations else "degraded",
                summary=draft.summary,
                findings=draft.findings,
                suspected_locations=draft.suspected_locations,
                sources=draft.sources,
                contradictions=draft.contradictions,
                elapsed_ms=elapsed_ms,
                tool_calls=tool_calls,
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                raw_preview=json.dumps(raw, ensure_ascii=False, default=str)[:2000],
            )
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return AgentEvidence(
                id=f"E-{self.name}",
                agent=self.name,
                status="error",
                summary=f"{self.name} 执行失败",
                errors=[f"{type(exc).__name__}: {exc}"],
                elapsed_ms=elapsed_ms,
            )
