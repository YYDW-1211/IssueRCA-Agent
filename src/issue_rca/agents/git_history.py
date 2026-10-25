from __future__ import annotations

from typing import Any

from issue_rca.agents.base import BaseEvidenceAgent
from issue_rca.models import InvestigationPlan, IssueContext


class GitHistoryAgent(BaseEvidenceAgent):
    name = "git_history"

    @staticmethod
    def _compact_commit(item: Any, index: int) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise TypeError(f"Git commit #{index} 必须是 object，实际为 {type(item).__name__}")
        sha = item.get("sha")
        if not isinstance(sha, str) or not sha:
            raise ValueError(f"Git commit #{index} 缺少有效 sha")
        commit = item.get("commit")
        if not isinstance(commit, dict):
            raise TypeError(
                f"Git commit #{index}.commit 必须是 object，实际为 {type(commit).__name__}"
            )
        author = commit.get("author")
        if author is None:
            author = {}
        if not isinstance(author, dict):
            raise TypeError(
                f"Git commit #{index}.commit.author 必须是 object 或 null，实际为 "
                f"{type(author).__name__}"
            )
        return {
            "sha": sha,
            "message": commit.get("message"),
            "date": author.get("date"),
            "url": item.get("html_url"),
        }

    @staticmethod
    def _compact_comparison(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise TypeError(f"Git compare 结果必须是 object，实际为 {type(data).__name__}")
        files = data.get("files", [])
        if files is None:
            files = []
        if not isinstance(files, list):
            raise TypeError(f"Git compare.files 必须是 list，实际为 {type(files).__name__}")
        compact_files: list[dict[str, Any]] = []
        for index, file in enumerate(files[:10]):
            if not isinstance(file, dict):
                raise TypeError(
                    f"Git compare.files[{index}] 必须是 object，实际为 {type(file).__name__}"
                )
            compact_files.append(
                {
                    "filename": file.get("filename"),
                    "status": file.get("status"),
                    "additions": file.get("additions"),
                    "deletions": file.get("deletions"),
                    "patch": (file.get("patch") or "")[:4000],
                }
            )
        return {
            "status": data.get("status"),
            "ahead_by": data.get("ahead_by"),
            "files": compact_files,
        }

    async def gather(
        self, issue: IssueContext, plan: InvestigationPlan
    ) -> tuple[dict[str, Any], int]:
        commits = await self.repository.list_commits(issue.owner, issue.repo, limit=10)
        if not isinstance(commits, list):
            raise TypeError(
                f"RepositoryGateway.list_commits 必须返回 list，实际为 {type(commits).__name__}"
            )
        tool_calls = 1
        compact_commits = [self._compact_commit(item, index) for index, item in enumerate(commits)]

        comparison: dict[str, Any] = {}
        if len(compact_commits) >= 2:
            comparison_data = await self.repository.compare_commits(
                issue.owner,
                issue.repo,
                compact_commits[1]["sha"],
                compact_commits[0]["sha"],
            )
            tool_calls += 1
            comparison = self._compact_comparison(comparison_data)

        return {
            "search_terms": plan.search_terms,
            "recent_commits": compact_commits,
            "latest_commit_comparison": comparison,
        }, tool_calls
