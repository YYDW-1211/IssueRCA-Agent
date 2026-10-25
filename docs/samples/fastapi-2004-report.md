# IssueRCA 报告

## Issue

- 仓库：`fastapi/fastapi`
- Issue：[Allow JSON payload for GET calls](https://github.com/fastapi/fastapi/issues/2004)
- 状态：`closed`
- Run ID：`20261025T140345Z-011626ed`

## 执行摘要

Issue #2004 is not a FastAPI server-side defect: FastAPI already parses JSON bodies on GET routes (verified via curl), while the reported TypeError originates in the browser fetch/Swagger UI layer. The real constraint is that OpenAPI 3.0 forbids requestBody on GET/DELETE/HEAD, so Swagger UI cannot render or send a body for GET operations. The issue was closed as a Swagger UI/OpenAPI limitation, and no FastAPI core change was required.

## Top-1 根因

**置信度：82%**

FastAPI classifies a Pydantic model parameter as a body parameter regardless of HTTP method (fastapi/dependencies/utils.py), so a GET route declaring `sample: Sample` produces a body requirement. OpenAPI 3.0 forbids requestBody on GET/DELETE/HEAD, so the generated schema (fastapi/openapi/utils.py) cannot expose a body field for that operation; Swagger UI therefore offers no body input, and the browser fetch API rejects any GET request carrying a body. The server-side parsing path itself never rejects GET bodies, which is why curl succeeds while the browser fails.

## 可疑代码位置

1. `fastapi/openapi/utils.py` (`get_openapi_path / get_openapi_operation_request_body`) — 置信度 60%：Attaches requestBody per operation; under OpenAPI 3.0 this is disallowed for GET/DELETE/HEAD, so the generated schema cannot expose a GET body and Swagger UI cannot render one. This is the actual limitation behind the issue.
2. `fastapi/dependencies/utils.py` (`get_dependant / analyze_param`) — 置信度 55%：Classifies Pydantic model parameters as body params without gating on HTTP method, which is why a GET route with a Pydantic model yields a body requirement. This path already supports GET bodies server-side.
3. `fastapi/routing.py` (`APIRoute.get_request_handler`) — 置信度 35%：Request body parsing entry point; expected to contain no method-based rejection of bodies, consistent with curl succeeding on GET with a JSON body.
4. `fastapi/params.py` (`Body`) — 置信度 25%：Defines the Body marker used to explicitly declare body params on non-POST routes; relevant to how users express GET body parameters.

## 支持证据

- A curl GET request with a JSON body succeeds against a FastAPI GET route, proving server-side body parsing already works.
- The reported TypeError text ('Request with GET/HEAD method cannot have body') is emitted by the browser fetch API, not by FastAPI.
- OpenAPI 3.0 forbids requestBody on GET/DELETE/HEAD, so Swagger UI cannot render a body input for GET operations.
- FastAPI classifies Pydantic model parameters as body params independent of HTTP method, producing a body requirement for the example GET route.
- Maintainer Kludex stated the request cannot be solved in FastAPI and referenced prior discussions #713, #1691, and #1626.
- The issue was closed by the reporter after being redirected to the Swagger UI limitation (swagger-api/swagger-ui#2136).
- No recent commit in the 0.160.0–0.162.0 range changes GET body routing or introduces method-based body rejection.

## 反向证据

- Commenters initially cited RFC 2616 as forbidding GET bodies, then corrected to RFC 7231, which permits but discourages them.
- OpenAPI 3.0 forbids requestBody on GET while OpenAPI 3.1 permits it, creating version-dependent expectations for the same route.

## 已排除假设

- FastAPI routing or body parsing rejects GET bodies: contradicted by successful curl GET requests with JSON payloads.
- A recent FastAPI regression broke GET body handling: no commit in the reviewed range touches GET body routing or method-based rejection.
- The failure is caused by Pydantic validation of the model: the error occurs before any request is sent, in the browser fetch layer.

## 建议验证步骤

1. Reproduce with `curl -X GET` plus a JSON body against the example route to confirm server-side acceptance.
2. Inspect the generated OpenAPI schema for the GET operation to confirm requestBody is absent under OpenAPI 3.0.
3. Repeat the request from Swagger UI to confirm the body input is not rendered for the GET operation.
4. Check behavior under OpenAPI 3.1 generation to see whether requestBody is emitted for GET.
5. Confirm no method-based body rejection exists in fastapi/routing.py request handling.

## 不确定性

Exact line numbers in the cited files were not provided by the agents, so locations are file/symbol-level only. Whether FastAPI's OpenAPI 3.1 output now emits GET requestBody was not directly verified.

## 运行指标

| 指标 | 数值 |
|---|---:|
| 端到端耗时 | 30072 ms |
| GitHub/Composio 工具调用 | 21 |
| LLM Prompt Tokens | 36908 |
| LLM Completion Tokens | 5638 |

### 节点耗时

```json
{
  "load_issue": 2813,
  "lead_plan": 9159,
  "code_search": 10525,
  "documentation": 11102,
  "git_history": 8710,
  "issue_history": 7667,
  "lead_synthesis": 6980
}
```

### 错误与降级

- 无
