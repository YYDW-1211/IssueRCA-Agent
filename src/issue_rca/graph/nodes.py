from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from issue_rca.agents.base import BaseEvidenceAgent
from issue_rca.agents.lead import LeadAgent
from issue_rca.models import MetricEvent, RCAReport, RunMetrics


def make_load_issue_node(github_client: Any) -> Callable:
    async def load_issue(state):
        started = time.perf_counter()
        issue = await github_client.fetch_issue(state["issue_url"])
        return {
            "issue": issue,
            "metric_events": [
                MetricEvent(
                    source="load_issue",
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    tool_calls=3,
                )
            ],
        }

    return load_issue


def make_plan_node(lead: LeadAgent) -> Callable:
    async def create_plan(state):
        started = time.perf_counter()
        result = await lead.create_plan(state["issue"])
        return {
            "plan": result.data,
            "metric_events": [
                MetricEvent(
                    source="lead_plan",
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    prompt_tokens=result.usage.prompt_tokens,
                    completion_tokens=result.usage.completion_tokens,
                )
            ],
        }

    return create_plan


def make_agent_node(agent: BaseEvidenceAgent) -> Callable:
    async def run_agent(state):
        evidence = await agent.run(state["issue"], state["plan"])
        return {
            "evidences": [evidence],
            "metric_events": [
                MetricEvent(
                    source=evidence.agent,
                    elapsed_ms=evidence.elapsed_ms,
                    tool_calls=evidence.tool_calls,
                    prompt_tokens=evidence.prompt_tokens,
                    completion_tokens=evidence.completion_tokens,
                    errors=evidence.errors,
                )
            ],
        }

    return run_agent


def make_synthesis_node(lead: LeadAgent) -> Callable:
    async def synthesize(state):
        evidences = state.get("evidences", [])
        if not evidences or all(item.status == "error" for item in evidences):
            raise RuntimeError("所有 Sub-Agent 均执行失败，无法生成可信 RCA")

        started = time.perf_counter()
        result = await lead.synthesize(state["issue"], state["plan"], evidences)
        synth_event = MetricEvent(
            source="lead_synthesis",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
        )
        events = [*state.get("metric_events", []), synth_event]
        metrics = RunMetrics(
            total_elapsed_ms=int((time.perf_counter() - state["started_perf"]) * 1000),
            agent_elapsed_ms={event.source: event.elapsed_ms for event in events},
            model_prompt_tokens=sum(event.prompt_tokens for event in events),
            model_completion_tokens=sum(event.completion_tokens for event in events),
            tool_calls=sum(event.tool_calls for event in events),
            errors=[error for event in events for error in event.errors],
        )
        report = RCAReport(
            run_id=state["run_id"],
            issue=state["issue"],
            metrics=metrics,
            **result.data.model_dump(),
        )
        return {"report": report, "metric_events": [synth_event]}

    return synthesize
