from __future__ import annotations

import asyncio
from typing import Any

from issue_rca.agents.base import BaseEvidenceAgent
from issue_rca.models import InvestigationPlan, IssueContext


class IssueHistoryAgent(BaseEvidenceAgent):
    name = "issue_history"

    async def gather(
        self, issue: IssueContext, plan: InvestigationPlan
    ) -> tuple[dict[str, Any], int]:
        queries = (plan.search_terms or [issue.title])[:3]
        groups = await asyncio.gather(
            *[
                self.repository.search_issues(issue.owner, issue.repo, query, limit=5)
                for query in queries
            ]
        )
        unique: dict[int, dict[str, Any]] = {}
        for group in groups:
            for item in group:
                number = int(item.get("number", -1))
                if number > 0 and number != issue.number:
                    unique[number] = {
                        "number": number,
                        "title": item.get("title"),
                        "body": (item.get("body") or "")[:2000],
                        "state": item.get("state"),
                        "labels": item.get("labels", []),
                        "url": item.get("html_url"),
                    }
        return {"queries": queries, "historical_issues": list(unique.values())[:10]}, len(queries)
