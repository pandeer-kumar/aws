# GenAI 04 · Building a Production GenAI Platform on AWS (Multi-Team, Multi-Account)

> **Interviewer lens:**
> - "Ten product teams want to use LLMs. Design the platform: access, quotas, cost attribution, safety, observability."
> - "How do you do chargeback for tokens per team?"
> - "How do you make a GenAI feature resilient to a regional Bedrock throttling event?"

---

## 1. The "AI gateway" pattern

```
Team apps (many accounts) ──► AI Gateway (central platform account or per-region)
                                 • authN: IAM / JWT per team & app
                                 • per-team TOKEN BUCKETS (TPM/RPM) in ElastiCache/DynamoDB
                                 • model allow-list per team/data classification
                                 • routing: model choice, CRIS profile, fallback model, service tier
                                 • prompt caching defaults, max_tokens caps
                                 • Guardrails applied centrally (ApplyGuardrail / guardrailConfig)
                                 • logs: usage per team (tokens in/out/cached, cost), prompt logging per policy
                              ──► Bedrock (CRIS) · SageMaker endpoints · external providers if allowed
```
Built on: API Gateway / ALB + ECS/Lambda, or LiteLLM-style proxies on EKS, or AgentCore Gateway for tool access. **Bedrock application inference profiles** (you create them, tagged per team/app) give native cost allocation tags on model usage, often without a custom gateway for chargeback.

## 2. Cost attribution
- Application inference profiles tagged `team`, `app`, `env` → Cost Explorer/CUR by tag.
- Or gateway-side metering: write token usage events → Firehose → S3 → Athena → per-team showback.
- Unit metrics: cost per conversation, per resolved ticket, per document processed.

## 3. Resilience for GenAI

| Risk | Mitigation |
|---|---|
| Throttling / capacity in a region | CRIS (geo/global), multi-region gateway, fallback model, queue non-interactive work, batch |
| Model deprecation | Abstraction (Converse API), eval suite to qualify replacements, track lifecycle dates |
| Provider/model outage | Secondary model family pre-qualified by evals; feature flag to switch |
| Latency spikes | Streaming, timeouts with partial responses, smaller-model fallback |
| Quality regression | Online evals with alarms; canary prompts/models |

## 4. Governance checklist
- Data classification → allowed models/regions (SCPs + gateway policy).
- Invocation logging policy (what is logged, retention, encryption, access).
- Guardrails baseline per risk tier; red-team before launch; incident process for harmful outputs.
- Human-in-the-loop for consequential decisions.
- Model cards / AI Service Cards reviewed; ISO 42001 / EU AI Act obligations where relevant.

## 5. Interview question — model answer

<details><summary><b>Design LLM access for 10 teams with chargeback, safety and no single team starving others.</b></summary>

Central AI gateway per region (ECS Fargate behind ALB with JWT/IAM auth), token-bucket quotas per team in DynamoDB/ElastiCache sized from the account's Bedrock TPM (reserve headroom for priority apps). Model allow-lists by data class. Default prompt caching and max_tokens limits. Guardrails baseline applied centrally, with stricter team-specific ones on top. Application inference profiles per team for native cost tags, plus gateway usage logs to S3/Athena for unit metrics. Failover: CRIS profiles + secondary region gateway + fallback model qualified by evals. Observability: per-team latency/TTFT/throttle/guardrail-intervention dashboards. A self-service onboarding pipeline creates the team's profile, quota and IAM role.

</details>

<!-- nav -->
---

← [GenAI 03 · Agents on AWS: Bedrock Agents, AgentCore, MCP, and Running Agents in Production](03-agents-and-agentcore.md) · [🏠 Home](../README.md) · [Senior AWS Question Bank (as asked when hiring someone with ~10 years of experience)](../05-interview-prep/01-senior-question-bank.md) →
