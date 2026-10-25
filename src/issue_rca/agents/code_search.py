from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from issue_rca.agents.base import BaseEvidenceAgent
from issue_rca.integrations.lsp import LspSearchProvider, NullLspSearchProvider
from issue_rca.models import InvestigationPlan, IssueContext


class CodeSearchAgent(BaseEvidenceAgent):
    name = "code_search"

    def __init__(
        self,
        repository,
        llm,
        max_files: int = 10,
        lsp: LspSearchProvider | None = None,
        local_repo_path: Path | None = None,
    ) -> None:
        super().__init__(repository, llm)
        self.max_files = max_files
        self.lsp = lsp or NullLspSearchProvider()
        self.local_repo_path = local_repo_path

    async def gather(
        self, issue: IssueContext, plan: InvestigationPlan
    ) -> tuple[dict[str, Any], int]:
        terms = (plan.search_terms or [issue.title])[:3]
        result_groups = await asyncio.gather(
            *[
                self.repository.search_code(issue.owner, issue.repo, term, self.max_files)
                for term in terms
            ]
        )
        tool_calls = len(terms)
        candidates: dict[str, dict[str, Any]] = {}
        for items in result_groups:
            for item in items:
                path = item.get("path")
                if path and path not in candidates:
                    candidates[path] = item
                if len(candidates) >= self.max_files:
                    break

        selected = list(candidates.values())[: min(self.max_files, 5)]
        contents = await asyncio.gather(
            *[
                self.repository.get_file_content(
                    issue.owner, issue.repo, item["path"], issue.default_branch
                )
                for item in selected
            ],
            return_exceptions=True,
        )
        tool_calls += len(selected)
        files: list[dict[str, Any]] = []
        for item, content in zip(selected, contents, strict=False):
            files.append(
                {
                    "path": item.get("path"),
                    "url": item.get("html_url"),
                    "content": (
                        f"ERROR: {content}"
                        if isinstance(content, Exception)
                        else str(content)[:5000]
                    ),
                }
            )

        lsp_symbols: list[dict[str, Any]] = []
        lsp_status = "disabled_no_local_repo"
        lsp_missing_files: list[str] = []
        if self.local_repo_path is not None:
            repository_root = self.local_repo_path.expanduser().resolve()
            if not repository_root.exists():
                raise FileNotFoundError(f"LOCAL_REPO_PATH 不存在: {repository_root}")
            if not repository_root.is_dir():
                raise NotADirectoryError(f"LOCAL_REPO_PATH 不是目录: {repository_root}")

            local_files: list[str] = []
            for item in selected:
                relative_path = str(item["path"]).replace("\\", "/").lstrip("/")
                candidate = (repository_root / relative_path).resolve()
                try:
                    candidate.relative_to(repository_root)
                except ValueError:
                    lsp_missing_files.append(relative_path)
                    continue
                if candidate.is_file():
                    local_files.append(relative_path)
                else:
                    lsp_missing_files.append(relative_path)

            if local_files:
                lsp_symbols = await self.lsp.document_symbols(repository_root, local_files)
                lsp_status = "used" if lsp_symbols else "available_no_symbols"
            else:
                lsp_status = "skipped_no_matching_local_files"

        return {
            "queries": terms,
            "candidate_files": files,
            "lsp_symbols": lsp_symbols,
            "lsp_status": lsp_status,
            "lsp_missing_files": lsp_missing_files,
        }, tool_calls
