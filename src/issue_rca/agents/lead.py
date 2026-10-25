from __future__ import annotations

from issue_rca.integrations.deepseek import LLMProvider
from issue_rca.models import AgentEvidence, InvestigationPlan, IssueContext, LLMResult, RCADraft


class LeadAgent:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def create_plan(self, issue: IssueContext) -> LLMResult:
        result = await self.llm.plan(issue)
        result.data = InvestigationPlan.model_validate(result.data)
        return result

    async def synthesize(
        self,
        issue: IssueContext,
        plan: InvestigationPlan,
        evidences: list[AgentEvidence],
    ) -> LLMResult:
        result = await self.llm.synthesize(issue, plan, evidences)
        result.data = RCADraft.model_validate(result.data)
        return result
