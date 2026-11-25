# GenAI 03 · Agents on AWS: Bedrock Agents, AgentCore, MCP, and Running Agents in Production

> **Interviewer lens (you work on agentic systems, so expect depth here):**
> - "Bedrock Agents vs AgentCore vs 'my own LangGraph/Strands on ECS': when each?"
> - "How is an agent session isolated in AgentCore Runtime, and why does that matter for security?"
> - "How do agents get credentials to call third-party APIs *on behalf of a user*?"
> - "How do you stop an agent from calling a dangerous tool, deterministically, not via prompt?"
> - "How do you evaluate and monitor an agent in production?"
> - "Prompt injection through a retrieved document makes your agent email data out. Defend."

---

## 1. Options for running agents on AWS

| Option | What you get | You own | Choose when |
|---|---|---|---|
| **Bedrock Agents** (managed) | Config-driven agent: instructions, action groups (OpenAPI + Lambda), Knowledge Bases, return-of-control, multi-agent collaboration (supervisor/collaborators), memory | Lambdas, prompts | Fast, simple, mostly-AWS tool calls, little custom orchestration |
| **Bedrock AgentCore** (agent infrastructure) | Runtime, Memory, Gateway, Identity, Code Interpreter, Browser, Observability, Policy, Evaluations, **framework-agnostic** (Strands, LangGraph, CrewAI, LlamaIndex, OpenAI Agents SDK, custom) and model-agnostic | Agent code and framework | Production agents with custom logic, any framework, enterprise security |
| **DIY on ECS/EKS/Lambda** | Everything | Everything: isolation, scaling, memory, auth, observability | Special constraints, existing platform |

**Strands Agents** is AWS's open-source agent SDK (model-driven loop, tools as Python functions, MCP support). It pairs naturally with AgentCore but isn't required.

---

## 2. AgentCore components: how they work

> Status as of late 2026: Runtime, Memory, Gateway, Identity, Code Interpreter, Browser and Observability are GA (since Oct 2025). **Policy** and **Evaluations** went GA in March 2026. Check the AgentCore release notes for new regions and features.

### 2.1 Runtime
```
Client ──InvokeAgentRuntime(sessionId, payload)──► AgentCore Runtime
   per SESSION: a dedicated microVM (isolated CPU, memory, filesystem)
   your container/code (any framework) runs inside; long-running sessions supported (hours)
   session ends / idle timeout → microVM destroyed, memory sanitized
```
- **Session isolation in microVMs** is the key security property. Agent code often runs tools, writes files, executes model-generated code. Per-session microVMs mean one user's session can't see another's state. That would be a real risk on a shared container fleet.
- Serverless scaling; you pay for actual CPU/memory consumed (I/O wait while waiting on the LLM is cheap).
- Supports HTTP, streaming, WebSocket/bidirectional streaming, **MCP servers** and **A2A** (agent-to-agent) protocol hosting.
- Deploy as a container image (ECR) or via direct code upload (Python, Node.js). Versions and endpoints (aliases) for safe rollout.
- Inbound auth: IAM SigV4 or **JWT** from your IdP (Cognito, Okta, Entra ID).
- Can attach to your VPC; bring-your-own file systems (S3 Files / EFS) for persistent workspaces.

### 2.2 Memory
- **Short-term**: raw conversation events per session/actor.
- **Long-term**: asynchronous **extraction strategies** (semantic facts, user preferences, session summaries, custom/self-managed strategies) into **namespaces** (e.g. `/users/{actorId}/preferences`). Retrieval by semantic search.
- Direct ingestion APIs for long-term records; fine-grained access control (e.g. Cedar policies through Gateway) for per-user/tenant isolation.
- Design point: memory is **multi-tenant data**. Namespace by tenant/user and enforce access at the platform, not in the prompt.

### 2.3 Gateway
```
Agent ──MCP──► AgentCore Gateway ──► targets:
                   • OpenAPI specs (REST APIs)      • Lambda functions
                   • Smithy models (AWS APIs)        • existing MCP servers
                   • connectors (e.g. Step Functions state machines as tools)
   handles: inbound auth (JWT/IAM), outbound auth (API keys/OAuth via Identity),
            semantic TOOL SEARCH (agent finds the right tool among hundreds), protocol translation
```
- Turns existing APIs into **MCP tools** without writing MCP servers.
- **Tool search** matters at scale: putting 300 tool schemas in the prompt costs tokens and hurts accuracy. Search retrieves the relevant few.

### 2.4 Identity
- **Inbound**: who is calling the agent (user JWT, IAM).
- **Outbound**: how the agent calls tools *as the user* or *as itself*:
  - **OAuth 2.0 user-delegated (3-legged)**: user consents once, Identity stores refresh tokens in a **token vault** (KMS-encrypted), and the agent gets scoped access tokens at tool-call time. Agent code never sees long-lived secrets.
  - 2-legged (client credentials) / API keys (can reference Secrets Manager secrets).
- **Workload identity** for each agent; consent flows for end users.

### 2.5 Code Interpreter & Browser
- **Code Interpreter**: sandboxed execution (Python/JS/TS) for data analysis or file processing. Network modes (sandbox / public / VPC). Never run model-generated code in your app container.
- **Browser**: managed, isolated headless browser sessions for web-navigating agents. Live view, session recording, Playwright/CDP compatible.

### 2.6 Observability
- OpenTelemetry-based traces/spans of agent steps (model calls, tool calls, memory, latency, tokens) into **CloudWatch** (GenAI observability dashboards). Works with any framework via OTEL instrumentation.

### 2.7 Policy (deterministic guardrails for tools)
- Policies written in **Cedar** (or authored from natural language, then compiled to Cedar) and attached to Gateway. **Every tool call is evaluated before it reaches the target**: allow or deny based on principal, tool, and *parameters* (e.g. `refund.amount <= 500` unless role is supervisor).
- This is the answer to "how do you stop a dangerous action deterministically?" The LLM can be tricked; the policy engine can't be prompted.

### 2.8 Evaluations
- Built-in evaluators (correctness, helpfulness, faithfulness, tool selection accuracy, goal success, harmfulness…) plus custom LLM-as-judge evaluators.
- **Online** (sample and score live production traces continuously) and **on-demand** (test sets in CI). Alarms on quality score drops, the agent equivalent of SLOs.

---

## 3. Reference architecture: enterprise support agent

```
User (web app, Cognito/Okta login)
  │ JWT
  ▼
API Gateway / ALB ──► AgentCore Runtime (Strands agent; per-session microVM; in VPC)
                         │ model calls: Bedrock (Converse, CRIS profile, prompt caching, Guardrails)
                         │ memory: AgentCore Memory (namespace /tenant/{t}/user/{u})
                         │ knowledge: Bedrock Knowledge Base (ACL metadata filter)
                         │ tools via MCP ──► AgentCore Gateway ──Policy (Cedar)──►
                         │                      • Orders API (OpenAPI target, OAuth on behalf of user)
                         │                      • Refund Lambda (policy: amount ≤ $500)
                         │                      • Ticketing SaaS (OAuth via Identity token vault)
                         │ code: Code Interpreter (CSV analysis)
                         ▼
            Observability (OTEL → CloudWatch) · Evaluations (online, sampled) · CloudTrail
            Human-in-the-loop: high-risk actions → return of control / approval step (Step Functions waitForTaskToken)
```

---

## 4. Agent-specific failure modes (and how to keep agents working)

| Failure | Symptom | Mitigation |
|---|---|---|
| **Infinite / long loops** | Cost spike, timeouts | Max iterations, token/time budget per session, loop detection |
| Tool-selection errors | Wrong API called | Fewer, well-described tools; tool search; evals on tool accuracy |
| **Prompt injection** (direct or indirect via retrieved docs/web/tool output) | Agent follows attacker text | Treat tool/retrieval output as untrusted data; **Cedar policy** on actions; least-privilege tool credentials (user-scoped OAuth); human approval for irreversible actions; Guardrails prompt-attack filter; output egress controls (no arbitrary URLs/email recipients) |
| Hallucinated tool parameters | 4xx from APIs, bad writes | JSON schema validation, idempotency keys on tool APIs, dry-run modes |
| Context window bloat | Latency/cost growth per turn | Summarize history, memory extraction, prompt caching |
| Model throttling mid-task | Half-finished workflows | Retries with backoff, model fallback, durable state (checkpoint steps) |
| Non-determinism | Flaky behaviour after model update | Pin model versions; eval suite before upgrading; canary new prompts/models |
| Data leakage across tenants | Memory/KB returns other tenant data | Namespaces + platform-enforced filters, per-session microVMs |

**Security principle to say out loud:** the agent's permissions = the **intersection** of what the *user* may do and what the *agent* is allowed to do. Enforce that outside the model (IAM/OAuth scopes + Cedar policy), never in the system prompt alone.

---

## 5. Senior interview questions — model answers

<details><summary><b>Q1. Why per-session microVM isolation instead of a shared container fleet?</b></summary>
Agents execute untrusted, model-directed actions: running code, writing files, holding user tokens in memory. On a shared container, a compromised or confused session could read another user's files, environment or tokens (cross-session leakage), and state could persist between users. Per-session microVMs (Firecracker-style) give hardware-virtualization boundaries, a clean environment per session and sanitization on teardown. The trade-off is a per-session start cost and resource overhead, which the managed runtime absorbs.
</details>

<details><summary><b>Q2. An agent needs to read a user's Google Drive and create Jira tickets. How do credentials work?</b></summary>
3-legged OAuth via AgentCore Identity: the first time, the user is redirected to consent; the refresh token is stored in the token vault keyed to (agent workload identity, user). At tool-call time Gateway/Identity exchanges it for a short-lived, scoped access token and attaches it to the outbound call. The agent code never handles long-lived secrets. Scopes are minimal (read-only Drive, create-issue Jira). Revocation is via the IdP/provider, and the audit trail shows which user the agent acted for.
</details>

<details><summary><b>Q3. How do you ship a new prompt/model version for an agent safely?</b></summary>
Treat it like code: version prompts in Git, run the offline eval suite (golden conversations, tool-accuracy tests, safety red-team set) in CI with pass thresholds, deploy to a new Runtime endpoint version, canary a small % of sessions, compare online evaluation scores + cost/latency + escalation rate against the baseline, then promote or roll back. Pin model IDs, never "latest".
</details>

<details><summary><b>Q4. Cost blew up: one agent session used 2M tokens.</b></summary>
Likely a loop or context bloat (tool returning huge payloads that get appended each turn). Fixes: per-session token/time budgets enforced in the loop, truncate/summarize tool outputs, paginate tool results, prompt caching for the stable prefix, alarms on tokens per session (CloudWatch metrics from observability spans), and a kill switch.
</details>
