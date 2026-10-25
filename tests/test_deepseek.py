from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from issue_rca.integrations.deepseek import (
    DeepSeekLLM,
    _extract_json,
    build_synthesis_payload,
)
from issue_rca.integrations.mock import load_demo_data
from issue_rca.models import (
    AgentEvidence,
    BugHypothesis,
    InvestigationPlan,
    IssueContext,
    SuspectedLocation,
)


def valid_plan_json(summary: str = "summary") -> str:
    return json.dumps(
        {
            "issue_summary": summary,
            "hypotheses": [
                {
                    "id": "H1",
                    "statement": "guard missing",
                    "rationale": "trace",
                    "confidence": 0.8,
                    "keywords": ["coupon"],
                    "verification_steps": ["inspect"],
                }
            ],
            "search_terms": ["coupon"],
            "target_areas": ["checkout"],
            "agent_tasks": {"code_search": "search"},
        },
        ensure_ascii=False,
    )


class FakeResponses:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses) -> None:
        self.responses = FakeResponses(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace())


def fake_response(
    content: str,
    *,
    status: str = "completed",
    incomplete_reason: str | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
):
    incomplete = (
        SimpleNamespace(reason=incomplete_reason) if incomplete_reason is not None else None
    )
    return SimpleNamespace(
        output_text=content,
        status=status,
        incomplete_details=incomplete,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def make_llm(responses, diagnostics_dir=None) -> DeepSeekLLM:
    return DeepSeekLLM(
        api_key="unused",
        base_url="https://example.invalid",
        model="fake",
        client=FakeClient(responses),
        diagnostics_dir=diagnostics_dir,
    )


def demo_issue() -> IssueContext:
    return IssueContext.model_validate(load_demo_data()["issue"])


def test_extract_json_accepts_escaped_quotes() -> None:
    parsed = _extract_json(r'{"message":"call \"foo\" safely"}')
    assert parsed == {"message": 'call "foo" safely'}


def test_extract_json_rejects_unescaped_quotes() -> None:
    with pytest.raises(json.JSONDecodeError):
        _extract_json('{"message":"call "foo" safely"}')


def test_extract_json_strips_markdown_and_repairs_trailing_comma() -> None:
    parsed = _extract_json('```json\n{"items":["a",],}\n```')
    assert parsed == {"items": ["a"]}


@pytest.mark.asyncio
async def test_deepseek_invalid_json_retries_once() -> None:
    client = FakeClient(
        [
            fake_response("not valid json"),
            fake_response(valid_plan_json()),
        ]
    )
    llm = DeepSeekLLM(
        api_key="unused",
        base_url="https://example.invalid",
        model="fake",
        client=client,
    )

    result = await llm.plan(demo_issue())

    assert result.data.hypotheses[0].id == "H1"
    assert len(client.responses.calls) == 2
    assert result.usage.prompt_tokens == 20
    assert client.responses.calls[0]["reasoning"] == {"effort": "none"}
    assert client.responses.calls[0]["text"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_missing_required_field_is_retried() -> None:
    missing_field = json.dumps(
        {
            "hypotheses": [],
            "search_terms": [],
            "target_areas": [],
            "agent_tasks": {},
        }
    )
    llm = make_llm(
        [
            fake_response(missing_field),
            fake_response(valid_plan_json("recovered")),
        ]
    )

    result = await llm.plan(demo_issue())

    assert result.data.issue_summary == "recovered"
    assert len(llm._client.responses.calls) == 2


@pytest.mark.asyncio
async def test_truncated_response_is_not_accepted_and_writes_diagnostic(tmp_path) -> None:
    llm = make_llm(
        [
            fake_response(
                '{"issue_summary":"partial',
                status="incomplete",
                incomplete_reason="max_output_tokens",
                output_tokens=1200,
            ),
            fake_response(
                '{"issue_summary":"still partial',
                status="incomplete",
                incomplete_reason="max_output_tokens",
                output_tokens=1200,
            ),
        ],
        diagnostics_dir=tmp_path,
    )

    with pytest.raises(ValueError, match="连续两次") as error:
        await llm.plan(demo_issue())

    assert "max_output_tokens" in str(error.value)
    diagnostics = list(tmp_path.glob("*.json"))
    assert len(diagnostics) == 2
    saved = json.loads(diagnostics[0].read_text(encoding="utf-8"))
    assert saved["status"] == "incomplete"
    assert saved["incomplete_reason"] == "max_output_tokens"
    assert saved["content_length"] > 0


@pytest.mark.asyncio
async def test_retry_exhaustion_does_not_return_partial_result(tmp_path) -> None:
    llm = make_llm(
        [fake_response("{"), fake_response('{"issue_summary": "broken"')],
        diagnostics_dir=tmp_path,
    )

    with pytest.raises(ValueError, match="diagnostics="):
        await llm.plan(demo_issue())

    assert len(list(tmp_path.glob("*.json"))) == 2


@pytest.mark.asyncio
async def test_diagnostic_redacts_common_secret_patterns(tmp_path) -> None:
    llm = make_llm(
        [
            fake_response('{"bad":"ak_123456789secret"'),
            fake_response('{"bad":"github_pat_123456789secret"'),
        ],
        diagnostics_dir=tmp_path,
    )

    with pytest.raises(ValueError):
        await llm.plan(demo_issue())

    saved_text = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))
    assert "ak_123456789secret" not in saved_text
    assert "github_pat_123456789secret" not in saved_text
    assert "[REDACTED]" in saved_text


def synthesis_plan() -> InvestigationPlan:
    return InvestigationPlan(
        issue_summary="GET request body fails in browser tooling.",
        hypotheses=[
            BugHypothesis(
                id="H1",
                statement="Swagger UI cannot send GET bodies",
                rationale="Browser fetch rejects GET bodies before FastAPI receives them.",
                confidence=0.9,
                keywords=["requestBody"],
                verification_steps=["compare Swagger UI with curl"],
            )
        ],
        search_terms=["requestBody"],
        target_areas=["openapi"],
        agent_tasks={"code_search": "inspect OpenAPI generation"},
    )


def synthesis_evidence() -> list[AgentEvidence]:
    return [
        AgentEvidence(
            id="E-code_search",
            agent="code_search",
            summary="OpenAPI generation omits GET requestBody for compatibility.",
            findings=[
                "Swagger UI uses browser fetch.",
                "Direct curl requests reach FastAPI.",
                "Direct curl requests reach FastAPI.",
            ],
            suspected_locations=[
                SuspectedLocation(
                    path="fastapi/openapi/utils.py",
                    symbol="get_openapi_operation_request_body",
                    reason="Builds the OpenAPI requestBody metadata.",
                    confidence=0.9,
                )
            ],
            sources=["https://example.test/source"],
            raw_preview="raw tool output " * 300,
            elapsed_ms=123,
            tool_calls=8,
            prompt_tokens=1000,
            completion_tokens=500,
        )
    ]


def valid_rca_json() -> str:
    return json.dumps(
        {
            "executive_summary": (
                "The browser tooling rejects GET bodies before FastAPI receives them."
            ),
            "root_cause": (
                "Swagger UI uses browser fetch, which does not permit a GET request body."
            ),
            "confidence": 0.9,
            "suspected_locations": [
                {
                    "path": "fastapi/openapi/utils.py",
                    "symbol": "get_openapi_operation_request_body",
                    "reason": "Controls OpenAPI request body metadata.",
                    "confidence": 0.9,
                    "evidence_ids": ["E-code_search"],
                }
            ],
            "supporting_evidence": ["curl reaches FastAPI while browser fetch rejects the body"],
            "contradictory_evidence": [],
            "rejected_hypotheses": ["FastAPI cannot parse GET bodies"],
            "verification_steps": ["Compare the same request with curl and Swagger UI"],
            "uncertainty": "Behavior can vary across client tooling.",
        },
        ensure_ascii=False,
    )


def test_synthesis_payload_removes_raw_and_runtime_metadata() -> None:
    issue = demo_issue()
    evidence = synthesis_evidence()
    compact = build_synthesis_payload(issue, synthesis_plan(), evidence)
    serialized = json.dumps(compact, ensure_ascii=False)
    full = json.dumps(
        {
            "issue": issue.model_dump(mode="json"),
            "plan": synthesis_plan().model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in evidence],
        },
        ensure_ascii=False,
    )

    assert "raw_preview" not in serialized
    assert "prompt_tokens" not in serialized
    assert "completion_tokens" not in serialized
    assert "elapsed_ms" not in serialized
    assert "comments" not in compact["issue"]
    assert compact["evidence"][0]["findings"].count("Direct curl requests reach FastAPI.") == 1
    assert len(serialized) < len(full)


@pytest.mark.asyncio
async def test_synthesis_uses_dedicated_output_budget() -> None:
    client = FakeClient([fake_response(valid_rca_json(), output_tokens=700)])
    llm = DeepSeekLLM(
        api_key="unused",
        base_url="https://example.invalid",
        model="fake",
        max_tokens=1200,
        synthesis_max_tokens=1800,
        client=client,
    )

    result = await llm.synthesize(demo_issue(), synthesis_plan(), synthesis_evidence())

    assert result.data.suspected_locations[0].path == "fastapi/openapi/utils.py"
    assert client.responses.calls[0]["max_output_tokens"] == 1800


@pytest.mark.asyncio
async def test_synthesis_retries_truncation_then_validates_complete_result(tmp_path) -> None:
    client = FakeClient(
        [
            fake_response(
                '{"executive_summary":"partial',
                status="incomplete",
                incomplete_reason="max_output_tokens",
                output_tokens=1800,
            ),
            fake_response(valid_rca_json(), output_tokens=720),
        ]
    )
    llm = DeepSeekLLM(
        api_key="unused",
        base_url="https://example.invalid",
        model="fake",
        max_tokens=1200,
        synthesis_max_tokens=1800,
        client=client,
        diagnostics_dir=tmp_path,
    )

    result = await llm.synthesize(demo_issue(), synthesis_plan(), synthesis_evidence())

    assert result.data.root_cause
    assert len(client.responses.calls) == 2
    assert all(call["max_output_tokens"] == 1800 for call in client.responses.calls)
    diagnostics = list(tmp_path.glob("*RCADraft-attempt-1*.json"))
    assert len(diagnostics) == 1
