from __future__ import annotations

import asyncio
from typing import Any

from issue_rca.agents.base import BaseEvidenceAgent
from issue_rca.models import InvestigationPlan, IssueContext


class DocumentationAgent(BaseEvidenceAgent):
    name = "documentation"

    async def gather(
        self, issue: IssueContext, plan: InvestigationPlan
    ) -> tuple[dict[str, Any], int]:
        readme, tree = await asyncio.gather(
            self.repository.get_readme(issue.owner, issue.repo, issue.default_branch),
            self.repository.get_tree(issue.owner, issue.repo, issue.default_branch, recursive=True),
        )
        tool_calls = 2
        doc_paths = [
            item.get("path")
            for item in tree
            if item.get("type") == "blob"
            and isinstance(item.get("path"), str)
            and (
                item["path"].lower().startswith("docs/")
                or item["path"].lower() in {"contributing.md", "architecture.md"}
            )
        ][:3]
        doc_results = await asyncio.gather(
            *[
                self.repository.get_file_content(
                    issue.owner, issue.repo, path, issue.default_branch
                )
                for path in doc_paths
            ],
            return_exceptions=True,
        )
        tool_calls += len(doc_paths)
        docs = []
        for path, content in zip(doc_paths, doc_results, strict=False):
            docs.append(
                {
                    "path": path,
                    "content": (
                        f"ERROR: {content}"
                        if isinstance(content, Exception)
                        else str(content)[:4000]
                    ),
                }
            )
        return {
            "readme": readme[:6000],
            "documentation_files": docs,
            "target_areas": plan.target_areas,
        }, tool_calls
