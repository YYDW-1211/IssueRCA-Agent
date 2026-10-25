from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from issue_rca.agents.code_search import CodeSearchAgent
from issue_rca.agents.documentation import DocumentationAgent
from issue_rca.agents.git_history import GitHistoryAgent
from issue_rca.agents.issue_history import IssueHistoryAgent
from issue_rca.agents.lead import LeadAgent
from issue_rca.config import Settings
from issue_rca.graph.nodes import (
    make_agent_node,
    make_load_issue_node,
    make_plan_node,
    make_synthesis_node,
)
from issue_rca.graph.state import GraphState
from issue_rca.integrations.base import RepositoryGateway
from issue_rca.integrations.deepseek import LLMProvider
from issue_rca.integrations.lsp import MultilspySearchProvider, NullLspSearchProvider


@dataclass
class RuntimeServices:
    settings: Settings
    github: Any
    repository: RepositoryGateway
    llm: LLMProvider


def build_workflow(services: RuntimeServices):
    lead = LeadAgent(services.llm)
    lsp = (
        MultilspySearchProvider() if services.settings.local_repo_path else NullLspSearchProvider()
    )
    agents = {
        "code_search": CodeSearchAgent(
            services.repository,
            services.llm,
            max_files=services.settings.max_candidate_files,
            lsp=lsp,
            local_repo_path=services.settings.local_repo_path,
        ),
        "issue_history": IssueHistoryAgent(services.repository, services.llm),
        "git_history": GitHistoryAgent(services.repository, services.llm),
        "documentation": DocumentationAgent(services.repository, services.llm),
    }

    graph = StateGraph(GraphState)
    graph.add_node("load_issue", make_load_issue_node(services.github))
    graph.add_node("lead_plan", make_plan_node(lead))
    for name, agent in agents.items():
        graph.add_node(name, make_agent_node(agent))
    graph.add_node("lead_synthesis", make_synthesis_node(lead))

    graph.add_edge(START, "load_issue")
    graph.add_edge("load_issue", "lead_plan")
    for name in agents:
        graph.add_edge("lead_plan", name)
    graph.add_edge(list(agents), "lead_synthesis")
    graph.add_edge("lead_synthesis", END)
    return graph.compile()


async def run_workflow(issue_url: str, services: RuntimeServices) -> GraphState:
    graph = build_workflow(services)
    initial: GraphState = {
        "issue_url": issue_url,
        "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8],
        "started_at_iso": datetime.now(UTC).isoformat(),
        "started_perf": time.perf_counter(),
        "evidences": [],
        "metric_events": [],
    }
    return await graph.ainvoke(initial)
