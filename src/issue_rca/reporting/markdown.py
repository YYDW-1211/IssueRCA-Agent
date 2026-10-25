from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from issue_rca.models import RCAReport, RunArtifacts


def render_markdown(report: RCAReport) -> str:
    issue = report.issue
    locations = []
    for index, location in enumerate(report.suspected_locations, start=1):
        line = ""
        if location.line_start:
            line = f":{location.line_start}"
            if location.line_end and location.line_end != location.line_start:
                line += f"-{location.line_end}"
        symbol = f" (`{location.symbol}`)" if location.symbol else ""
        locations.append(
            f"{index}. `{location.path}{line}`{symbol} — "
            f"置信度 {location.confidence:.0%}：{location.reason}"
        )

    supporting = "\n".join(f"- {item}" for item in report.supporting_evidence) or "- 暂无"
    contradictory = "\n".join(f"- {item}" for item in report.contradictory_evidence) or "- 未发现"
    rejected = "\n".join(f"- {item}" for item in report.rejected_hypotheses) or "- 暂无"
    steps = "\n".join(f"{index}. {item}" for index, item in enumerate(report.verification_steps, 1))
    error_lines = "\n".join(f"- {item}" for item in report.metrics.errors) or "- 无"

    return f"""# IssueRCA 报告

## Issue

- 仓库：`{issue.repository}`
- Issue：[{issue.title}]({issue.url})
- 状态：`{issue.state}`
- Run ID：`{report.run_id}`

## 执行摘要

{report.executive_summary}

## Top-1 根因

**置信度：{report.confidence:.0%}**

{report.root_cause}

## 可疑代码位置

{chr(10).join(locations) if locations else "未定位到具体文件。"}

## 支持证据

{supporting}

## 反向证据

{contradictory}

## 已排除假设

{rejected}

## 建议验证步骤

{steps or "1. 补充复现信息后重新分析。"}

## 不确定性

{report.uncertainty}

## 运行指标

| 指标 | 数值 |
|---|---:|
| 端到端耗时 | {report.metrics.total_elapsed_ms} ms |
| GitHub/Composio 工具调用 | {report.metrics.tool_calls} |
| LLM Prompt Tokens | {report.metrics.model_prompt_tokens} |
| LLM Completion Tokens | {report.metrics.model_completion_tokens} |

### 节点耗时

```json
{json.dumps(report.metrics.agent_elapsed_ms, ensure_ascii=False, indent=2)}
```

### 错误与降级

{error_lines}
"""


def write_run_artifacts(
    report: RCAReport,
    plan: Any,
    evidences: list[Any],
    base_dir: Path,
) -> RunArtifacts:
    run_dir = base_dir / report.run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    report_json = run_dir / "report.json"
    report_markdown = run_dir / "report.md"
    trace_json = run_dir / "trace.json"

    report_json.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    report_markdown.write_text(render_markdown(report), encoding="utf-8")
    trace = {
        "run_id": report.run_id,
        "issue": report.issue.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan,
        "evidence": [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in evidences
        ],
        "metrics": report.metrics.model_dump(mode="json"),
    }
    trace_json.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    return RunArtifacts(
        run_dir=str(run_dir.resolve()),
        report_json=str(report_json.resolve()),
        report_markdown=str(report_markdown.resolve()),
        trace_json=str(trace_json.resolve()),
    )
