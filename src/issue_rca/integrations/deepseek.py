from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from issue_rca.models import (
    AgentEvidence,
    BugHypothesis,
    EvidenceDraft,
    InvestigationPlan,
    IssueContext,
    LLMResult,
    LLMUsage,
    RCADraft,
    SuspectedLocation,
)
from issue_rca.prompts.templates import (
    EVIDENCE_SYSTEM_PROMPTS,
    PLAN_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMProvider(Protocol):
    async def plan(self, issue: IssueContext) -> LLMResult: ...

    async def summarize_evidence(
        self,
        agent_name: str,
        issue: IssueContext,
        plan: InvestigationPlan,
        raw_evidence: dict[str, Any],
    ) -> LLMResult: ...

    async def synthesize(
        self,
        issue: IssueContext,
        plan: InvestigationPlan,
        evidences: list[AgentEvidence],
    ) -> LLMResult: ...

    async def check_connection(self) -> str: ...


class LLMOutputError(ValueError):
    pass


class LLMOutputTruncatedError(LLMOutputError):
    pass


@dataclass(frozen=True)
class _ResponseEnvelope:
    text: str
    status: str
    incomplete_reason: str | None
    prompt_tokens: int
    completion_tokens: int


def _strip_markdown_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _remove_trailing_commas(text: str) -> str:
    """Remove only commas directly before ] or } while outside JSON strings."""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                index += 1
                continue

        output.append(char)
        index += 1
    return "".join(output)


def _decode_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    cleaned = _strip_markdown_fence(text)
    if not cleaned:
        raise json.JSONDecodeError("Empty model response", cleaned, 0)

    candidates = [cleaned]
    first_object = cleaned.find("{")
    if first_object > 0:
        candidates.append(cleaned[first_object:])

    errors: list[json.JSONDecodeError] = []
    for candidate in candidates:
        for variant in (candidate, _remove_trailing_commas(candidate)):
            try:
                value, end = decoder.raw_decode(variant)
                trailing = variant[end:].strip()
                if trailing and trailing != "```":
                    raise json.JSONDecodeError("Unexpected trailing content", variant, end)
                if not isinstance(value, dict):
                    raise ValueError("模型输出不是 JSON object")
                return value
            except json.JSONDecodeError as exc:
                errors.append(exc)

    if errors:
        raise max(errors, key=lambda error: error.pos)
    raise ValueError("模型输出不是 JSON object")


def _extract_json(text: str) -> dict[str, Any]:
    """Backward-compatible parser entry point used by tests and diagnostics."""
    return _decode_json_object(text)


def _dedupe_strings(values: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def build_synthesis_payload(
    issue: IssueContext,
    plan: InvestigationPlan,
    evidences: list[AgentEvidence],
) -> dict[str, Any]:
    """Keep final synthesis evidence-rich without replaying raw tool payloads."""
    compact_evidence: list[dict[str, Any]] = []
    for evidence in evidences:
        locations = sorted(
            evidence.suspected_locations,
            key=lambda location: location.confidence,
            reverse=True,
        )[:4]
        compact_locations: list[dict[str, Any]] = []
        for location in locations:
            location_data = location.model_dump(mode="json")
            location_data["evidence_ids"] = _dedupe_strings(
                [*location.evidence_ids, evidence.id],
                limit=4,
            )
            compact_locations.append(location_data)

        compact_evidence.append(
            {
                "id": evidence.id,
                "agent": evidence.agent,
                "status": evidence.status,
                "summary": evidence.summary,
                "findings": _dedupe_strings(evidence.findings, limit=6),
                "suspected_locations": compact_locations,
                "sources": _dedupe_strings(evidence.sources, limit=5),
                "contradictions": _dedupe_strings(evidence.contradictions, limit=3),
                "errors": evidence.errors if evidence.status != "success" else [],
            }
        )

    return {
        "issue": {
            "url": issue.url,
            "repository": issue.repository,
            "number": issue.number,
            "title": issue.title,
            "body": issue.body[:6000],
            "labels": issue.labels,
        },
        "plan": {
            "issue_summary": plan.issue_summary,
            "hypotheses": [
                {
                    "id": hypothesis.id,
                    "statement": hypothesis.statement,
                    "rationale": hypothesis.rationale,
                    "confidence": hypothesis.confidence,
                }
                for hypothesis in plan.hypotheses[:4]
            ],
        },
        "evidence": compact_evidence,
    }


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"),
    re.compile(r"\b(?:sk|ak|ck)_[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}\b"),
)


def _redact_sensitive(text: str) -> str:
    redacted = text
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


class DeepSeekLLM:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 1200,
        synthesis_max_tokens: int = 1800,
        client: Any | None = None,
        diagnostics_dir: Path | None = None,
    ) -> None:
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._client = client
        self.model = model
        self.max_tokens = max_tokens
        self.synthesis_max_tokens = max(synthesis_max_tokens, max_tokens)
        self.diagnostics_dir = diagnostics_dir

    async def check_connection(self) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=4,
            temperature=0,
            reasoning_effort="none",
        )
        content = response.choices[0].message.content or ""
        return f"model={self.model}, response={content.strip()[:20]}"

    async def _request_structured(
        self,
        schema: type[ModelT],
        system_prompt: str,
        user_text: str,
        max_output_tokens: int,
    ) -> _ResponseEnvelope:
        schema_name = re.sub(r"[^A-Za-z0-9_-]", "_", schema.__name__)[:64]
        instructions = (
            f"{system_prompt}\n\n"
            "Return exactly one JSON object matching the supplied JSON Schema. "
            "Use every required field with the declared data type. "
            "Escape quotes, backslashes, and control characters inside strings. "
            "Do not use Markdown fences or add commentary outside the JSON. "
            "Keep explanations and list items concise."
        )
        response = await self._client.responses.create(
            model=self.model,
            instructions=instructions,
            input=user_text,
            max_output_tokens=max_output_tokens,
            reasoning={"effort": "none"},
            temperature=0.1,
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema.model_json_schema(),
                }
            },
        )
        usage = getattr(response, "usage", None)
        incomplete = getattr(response, "incomplete_details", None)
        return _ResponseEnvelope(
            text=getattr(response, "output_text", "") or "",
            status=str(getattr(response, "status", "unknown") or "unknown"),
            incomplete_reason=getattr(incomplete, "reason", None),
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )

    def _save_diagnostic(
        self,
        schema: type[BaseModel],
        attempt: int,
        envelope: _ResponseEnvelope,
        error: Exception,
    ) -> Path | None:
        if self.diagnostics_dir is None:
            return None
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{schema.__name__}-attempt-{attempt}-{uuid.uuid4().hex[:8]}.json"
        )
        path = self.diagnostics_dir / filename
        content = _redact_sensitive(envelope.text)
        diagnostic: dict[str, Any] = {
            "schema": schema.__name__,
            "attempt": attempt,
            "status": envelope.status,
            "incomplete_reason": envelope.incomplete_reason,
            "content_length": len(envelope.text),
            "prompt_tokens": envelope.prompt_tokens,
            "completion_tokens": envelope.completion_tokens,
            "error_type": type(error).__name__,
            "error": _redact_sensitive(str(error)),
            "content": content,
        }
        if isinstance(error, json.JSONDecodeError):
            start = max(0, error.pos - 160)
            end = min(len(content), error.pos + 160)
            diagnostic["json_error"] = {
                "message": error.msg,
                "line": error.lineno,
                "column": error.colno,
                "position": error.pos,
                "context": content[start:end],
            }
        path.write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _validate_envelope(envelope: _ResponseEnvelope) -> None:
        if envelope.status == "incomplete":
            reason = envelope.incomplete_reason or "unknown"
            raise LLMOutputTruncatedError(f"模型结构化输出不完整: {reason}")
        if envelope.status != "completed":
            raise LLMOutputError(f"模型结构化响应状态异常: {envelope.status}")
        if not envelope.text.strip():
            raise LLMOutputError("模型返回了空的结构化输出")

    async def _chat_json(
        self,
        schema: type[ModelT],
        system_prompt: str,
        payload: dict[str, Any],
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        payload_text = json.dumps(payload, ensure_ascii=False, default=str)
        total_prompt = 0
        total_completion = 0
        last_error: Exception | None = None
        diagnostic_paths: list[str] = []

        for attempt in range(1, 3):
            retry_instruction = ""
            if last_error is not None:
                retry_instruction = (
                    "\n\nThe previous generation failed validation. Regenerate the entire JSON "
                    "object from the original input; do not continue or copy a partial response. "
                    f"Validation failure: {type(last_error).__name__}: {last_error}"
                )
            envelope = await self._request_structured(
                schema,
                system_prompt,
                payload_text + retry_instruction,
                max_output_tokens=max_output_tokens or self.max_tokens,
            )
            total_prompt += envelope.prompt_tokens
            total_completion += envelope.completion_tokens
            try:
                self._validate_envelope(envelope)
                parsed = schema.model_validate(_decode_json_object(envelope.text))
                return LLMResult(
                    data=parsed,
                    usage=LLMUsage(
                        prompt_tokens=total_prompt,
                        completion_tokens=total_completion,
                    ),
                    raw_text=envelope.text,
                )
            except (
                json.JSONDecodeError,
                ValidationError,
                LLMOutputError,
                ValueError,
            ) as exc:
                last_error = exc
                diagnostic = self._save_diagnostic(schema, attempt, envelope, exc)
                if diagnostic is not None:
                    diagnostic_paths.append(str(diagnostic.resolve()))

        diagnostics = ", ".join(diagnostic_paths) if diagnostic_paths else "disabled"
        raise ValueError(
            "模型连续两次返回无效结构化 JSON: "
            f"{type(last_error).__name__}: {last_error}; diagnostics={diagnostics}"
        )

    async def plan(self, issue: IssueContext) -> LLMResult:
        return await self._chat_json(
            InvestigationPlan,
            PLAN_SYSTEM_PROMPT,
            {"issue": issue.model_dump(mode="json")},
        )

    async def summarize_evidence(
        self,
        agent_name: str,
        issue: IssueContext,
        plan: InvestigationPlan,
        raw_evidence: dict[str, Any],
    ) -> LLMResult:
        return await self._chat_json(
            EvidenceDraft,
            EVIDENCE_SYSTEM_PROMPTS[agent_name],
            {
                "issue": issue.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "raw_evidence": raw_evidence,
            },
        )

    async def synthesize(
        self,
        issue: IssueContext,
        plan: InvestigationPlan,
        evidences: list[AgentEvidence],
    ) -> LLMResult:
        return await self._chat_json(
            RCADraft,
            SYNTHESIS_SYSTEM_PROMPT,
            build_synthesis_payload(issue, plan, evidences),
            max_output_tokens=self.synthesis_max_tokens,
        )


class MockLLM:
    async def check_connection(self) -> str:
        return "mock DeepSeek provider ready"

    async def plan(self, issue: IssueContext) -> LLMResult:
        plan = InvestigationPlan(
            issue_summary=(
                "过期优惠券进入结算流程后未被转换为业务错误，导致空对象被解引用并返回 500。"
            ),
            hypotheses=[
                BugHypothesis(
                    id="H1",
                    statement="优惠券缓存未命中返回 None，但调用方直接访问 amount",
                    rationale="Issue 中的 NoneType/amount 与缓存重构评论直接指向空值校验缺失。",
                    confidence=0.82,
                    keywords=["coupon_cache", "amount", "invalid_coupon"],
                    verification_steps=["检查 apply_coupon 的缓存未命中分支"],
                ),
                BugHypothesis(
                    id="H2",
                    statement="路由层错误映射遗漏 InvalidCoupon",
                    rationale="预期为 400，实际为 500，也可能是异常未被 HTTP 层映射。",
                    confidence=0.45,
                    keywords=["checkout", "InvalidCoupon", "HTTP 400"],
                    verification_steps=["检查 checkout 异常处理和错误映射"],
                ),
            ],
            search_terms=["coupon_cache amount", "invalid_coupon", "apply_coupon"],
            target_areas=["coupon service", "checkout route", "error handling"],
            agent_tasks={
                "code_search": "定位优惠券读取和空值处理逻辑",
                "issue_history": "查找同类优惠券崩溃 Issue",
                "git_history": "检查缓存重构相关提交",
                "documentation": "确认过期优惠券的预期响应",
            },
        )
        return LLMResult(data=plan, usage=LLMUsage(prompt_tokens=120, completion_tokens=180))

    async def summarize_evidence(
        self,
        agent_name: str,
        issue: IssueContext,
        plan: InvestigationPlan,
        raw_evidence: dict[str, Any],
    ) -> LLMResult:
        locations: list[SuspectedLocation] = []
        findings: list[str] = []
        sources: list[str] = []
        contradictions: list[str] = []

        if agent_name == "code_search":
            locations = [
                SuspectedLocation(
                    path="src/checkout/coupon_service.py",
                    symbol="apply_coupon",
                    line_start=1,
                    line_end=5,
                    reason="coupon_cache.get(code) 可能返回 None，下一行直接访问 coupon.amount。",
                    confidence=0.94,
                )
            ]
            findings = ["缓存读取结果缺少 None 检查", "路由仅在存在 coupon 字段时调用 apply_coupon"]
        elif agent_name == "issue_history":
            findings = ["历史 Issue #17 描述了相同的 cache miss 后空对象解引用模式"]
            sources = ["https://github.com/example/checkout-service/issues/17"]
        elif agent_name == "git_history":
            locations = [
                SuspectedLocation(
                    path="src/checkout/coupon_service.py",
                    symbol="apply_coupon",
                    reason="缓存重构移除了 repository.find_active 后的空值异常分支。",
                    confidence=0.91,
                )
            ]
            findings = ["最近的 coupon cache refactor 删除了 InvalidCoupon 防护"]
        else:
            findings = ["README 约定过期或未知优惠券应返回 HTTP 400 / invalid_coupon"]

        draft = EvidenceDraft(
            summary=f"{agent_name} 已完成证据提取。",
            findings=findings,
            suspected_locations=locations,
            sources=sources,
            contradictions=contradictions,
        )
        return LLMResult(data=draft, usage=LLMUsage(prompt_tokens=80, completion_tokens=100))

    async def synthesize(
        self,
        issue: IssueContext,
        plan: InvestigationPlan,
        evidences: list[AgentEvidence],
    ) -> LLMResult:
        all_locations = [
            location for evidence in evidences for location in evidence.suspected_locations
        ]
        by_path: dict[str, SuspectedLocation] = {}
        for location in all_locations:
            existing = by_path.get(location.path)
            if existing is None or location.confidence > existing.confidence:
                by_path[location.path] = location
        locations = sorted(by_path.values(), key=lambda item: item.confidence, reverse=True)
        if not locations:
            locations = [
                SuspectedLocation(
                    path="src/checkout/coupon_service.py",
                    symbol="apply_coupon",
                    reason="Mock fallback candidate",
                    confidence=0.5,
                )
            ]
        draft = RCADraft(
            executive_summary="优惠券缓存重构引入了空值处理回归，使过期优惠券触发 500。",
            root_cause=(
                "apply_coupon 使用 coupon_cache.get 获取优惠券后，未判断返回值是否为 None，"
                "便直接读取 amount；重构同时删除了原有 InvalidCoupon 分支。"
            ),
            confidence=0.93,
            suspected_locations=locations,
            supporting_evidence=[finding for item in evidences for finding in item.findings],
            contradictory_evidence=[item for ev in evidences for item in ev.contradictions],
            rejected_hypotheses=["单纯由路由序列化导致 500：代码与提交证据更支持缓存空值回归"],
            verification_steps=[
                "添加 expired coupon 测试，断言返回 400 / invalid_coupon",
                "在 coupon_cache.get 后恢复空值检查并抛出 InvalidCoupon",
                "运行 checkout 与 coupon 相关测试集",
            ],
            uncertainty="Mock 数据未执行真实测试，最终结论需通过仓库测试验证。",
        )
        return LLMResult(data=draft, usage=LLMUsage(prompt_tokens=180, completion_tokens=220))
