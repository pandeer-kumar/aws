# GenAI 01 · Amazon Bedrock: How Inference Works, Scales, and Fails

> **Interviewer lens (senior AWS + GenAI roles, 2026):**
> - "Walk me through what happens on a `Converse` call: auth, routing, quotas, where my data goes."
> - "We get `ThrottlingException` at peak. What are *all* the levers?"
> - "Geographic vs global cross-Region inference: latency, data residency, and SCP implications?"
> - "On-demand vs provisioned throughput vs batch vs service tiers: when each?"
> - "How does prompt caching work and how much does it save on an agent loop?"
> - "How do you guarantee prompts never leave our VPC or get used for training?"

> **Freshness note:** Bedrock changes monthly. Verify model IDs, quotas, regions and prices on the Bedrock docs and What's New feed. Features below were checked against AWS announcements up to late 2026.

---

## 1. The request path

```
App (SDK, SigV4 or Bedrock API key) ──HTTPS──► bedrock-runtime.<region>.amazonaws.com
   (optionally via Interface VPC endpoint / PrivateLink, so traffic never touches the internet)
      │ 1. IAM authZ: bedrock:InvokeModel / InvokeModelWithResponseStream / Converse* on
      │    arn:aws:bedrock:<region>::foundation-model/<id>  or  an inference-profile ARN
      │ 2. Guardrail (if attached): input check
      │ 3. Quota check: requests/min and TOKENS/min per model per region (per account)
      │ 4. Route to model capacity:
      │      on-demand pool in region · cross-Region inference profile · provisioned throughput
      │ 5. Model runs in an AWS-operated "model deployment account"; providers (Anthropic, Meta…)
      │    have NO access to your prompts/outputs; not used for training; not stored by default
      │ 6. Guardrail output check → response (or stream)
      ▼
   CloudWatch metrics (Invocations, InputTokenCount, OutputTokenCount, InvocationLatency,
   InvocationThrottles) · optional model invocation logging (to S3/CloudWatch Logs) · CloudTrail
```

### APIs

| API | Use |
|---|---|
| **Converse / ConverseStream** | Unified chat API across models: messages, system prompt, **tool use** (function calling), images/documents, guardrails, prompt caching. **Default choice** |
| InvokeModel / InvokeModelWithResponseStream | Model-native JSON body (provider-specific) |
| Batch inference (`CreateModelInvocationJob`) | JSONL in S3 → results in S3, ~**50% cheaper**, hours of latency |
| Async invoke | Long-running generation (e.g. video) |
| `CountTokens` | Estimate tokens before sending |

---

## 2. Capacity, quotas and throttling (the #1 production problem)

On-demand quotas are **per account, per region, per model**: requests per minute (RPM) and **tokens per minute (TPM)**. Output tokens often count with a multiplier ("burndown rate"), and **max_tokens is reserved up front** at request start. A huge `max_tokens` eats quota even if the model writes a short answer.

### Levers when you see `ThrottlingException` (429)
1. **Right-size `max_tokens`** to what you actually need (frees reserved quota).
2. **Cross-Region inference (CRIS) profiles.** Bedrock routes to capacity in other regions of a geography (or globally). Higher effective throughput, often the biggest win.
3. **Prompt caching**: cached input tokens are cheaper and faster.
4. **Request a quota increase** (Service Quotas; justify with usage).
5. **Service tiers**: *Priority* (pay more for preferential processing), *Standard*, *Flex* (cheaper, can be deprioritized; for non-urgent work). Check model support.
6. **Provisioned Throughput**: purchased model units with a fixed throughput (needed for some custom models). A commitment.
7. **Batch inference** for anything not interactive (evals, backfills, classification).
8. **Client side**: exponential backoff with jitter (SDK adaptive retry mode), a **token-bucket limiter per tenant** in your gateway so one tenant can't starve others, queue + workers for async flows, and **model fallback** (smaller/cheaper model when the primary is throttled).
9. Smaller models for easy tasks (**intelligent prompt routing** between models in a family, or your own router).

---

## 3. Cross-Region inference (CRIS)

| | Geographic profile (e.g. `us.`, `eu.`, `apac.` prefix) | Global profile (`global.` prefix) |
|---|---|---|
| Routes to | Regions within that geography | Any supported commercial region worldwide |
| Data residency | Stays within the geography | Can be processed anywhere |
| Throughput | Higher than single region | Highest |
| Price | Same as source region on-demand | Typically ~10% cheaper (check pricing) |

Mechanics and gotchas:
- You call your **source region**. Bedrock picks the destination. Logs, Knowledge Bases, Guardrail configuration and data at rest stay in the source region; only the inference request/response moves (encrypted over AWS's network).
- **IAM**: the policy must allow `bedrock:InvokeModel*` on the **inference profile ARN** *and* on the foundation-model ARNs in every destination region the profile can route to.
- **SCP region deny-lists break CRIS.** Geographic profiles need the destination regions allowed. For **global** profiles, AWS documents that requests show `aws:RequestedRegion = "unspecified"`, so your SCP must allow that value for Bedrock actions (put Bedrock in a separate SCP statement so other services keep the region limit). To *forbid* global routing, deny on `inference-profile/global.*`.
- Compliance: for EU data residency use the `eu.` geographic profile, never `global.`.

---

## 4. Prompt caching

```
Request 1: [system prompt + tools + long doc] ◄cachePoint►  [user question]
            └──────── written to cache (cache-write tokens, priced slightly above input) ────────┘
Request 2 (within TTL, same prefix byte-for-byte): prefix READ from cache
            → cache-read tokens priced at a large discount (roughly 90% off input), lower latency
```
- Add **cache checkpoints** (`cachePoint` blocks in Converse) after stable content: system prompt, tool definitions, large documents, earlier conversation turns. Up to about **4 checkpoints** per request (model-dependent).
- **Minimum prefix length** per checkpoint (model-dependent, roughly 512–4,096 tokens). Shorter prefixes just don't cache.
- **TTL**: default **5 minutes**, reset on each hit. A **1-hour TTL** option exists for selected Claude models (since Jan 2026), billed at a higher cache-write rate.
- **Prefix must be identical.** Put dynamic content (timestamps, user names, retrieved chunks) *after* the checkpoint. A timestamp in the system prompt silently kills your cache hit rate.
- **Agent loops benefit hugely**: each step resends system + tools + history. Caching the growing history prefix turns O(n²) input cost into mostly cache reads.
- Monitor `CacheReadInputTokens` / `CacheWriteInputTokens` in the response usage and in metrics.

---

## 5. Security & privacy model

| Concern | Control |
|---|---|
| Data used for training? | No. Bedrock doesn't use prompts/outputs to train models or share them with providers |
| Network path | **Interface VPC endpoints** (`bedrock-runtime`, `bedrock-agent-runtime`, `bedrock-agentcore`…) + endpoint policies + `aws:SourceVpce` conditions |
| Who can use which model | IAM on model/profile ARNs; SCP to deny unapproved models (`bedrock:InvokeModel` with resource ARN conditions); **model access** enablement |
| Audit | CloudTrail (API calls); **model invocation logging** (full prompts/responses to S3/CW Logs, so encrypt it and restrict it, because that bucket now holds sensitive data) |
| Encryption | KMS CMKs for custom models, knowledge bases, agents, logs |
| Content safety & PII | **Guardrails** (below) |
| API keys | Bedrock API keys (short- or long-term) exist for quick starts. Prefer IAM roles; SCP-restrict long-term keys (`iam:CreateServiceSpecificCredential`) |

### Guardrails
Policies: **content filters** (hate, insults, sexual, violence, misconduct, **prompt attack** detection) with strength levels; **denied topics**; **word filters**; **sensitive information** (PII detect → block or mask; custom regex); **contextual grounding check** (is the answer grounded in the provided source and relevant to the query? scores with thresholds, which cuts hallucinations in RAG); **Automated Reasoning checks** (validate answers against formal logic rules generated from your policy documents, e.g. HR or insurance rules).
- Use via `guardrailConfig` in Converse, or **standalone `ApplyGuardrail`** on any text (including outputs from non-Bedrock models).
- Guardrails can be enforced across accounts via IAM condition `bedrock:GuardrailIdentifier` and organization-level policies.
- Trade-offs: added latency (especially streaming output checks) and false positives. Tune per use case and log interventions.

---

## 6. Customization ladder (cheapest first)
1. **Prompt engineering** + few-shot + structured output (tool schemas as JSON contracts).
2. **RAG** (Knowledge Bases) for knowledge. See [02-rag-and-knowledge-bases.md](./02-rag-and-knowledge-bases.md).
3. **Model distillation**: a big teacher model generates training data for a small student. Cheaper and faster for a narrow task.
4. **Fine-tuning** (supported models) / **continued pre-training**: for style/format/domain language, not for facts.
5. **Custom Model Import** (bring open-weight models such as Llama/Mistral/Qwen fine-tunes) and serve them on Bedrock (billed per model-unit-minute, scales to zero).
6. **SageMaker AI** (HyperPod / endpoints) when you need full control of training/serving.

---

## 7. Senior interview questions — model answers

<details><summary><b>Q1. Your chatbot's p95 latency is 9s. Break it down and fix it.</b></summary>

Measure: time-to-first-token (TTFT) vs total generation time vs retrieval time vs guardrail time vs your app. Fixes: **stream** (ConverseStream) so users see tokens at TTFT; prompt caching (cuts TTFT on long prefixes); a smaller/faster model for simple turns (router); fewer retrieved chunks and a reranker instead of stuffing 20 chunks; parallelize retrieval + guardrail input check; latency-optimized inference options where available; reduce output length (concise instructions, `max_tokens`); keep the client close to the region or use CRIS for capacity. Report TTFT and tokens/sec as SLIs.

</details>

<details><summary><b>Q2. Estimate monthly cost for an internal assistant: 5k users, 20 queries/day, 6k input / 500 output tokens per query.</b></summary>

Volume: 5,000 × 20 × 22 workdays = 2.2M queries/month. Tokens: 13.2B input, 1.1B output. With a mid-tier model at about $3/M input and $15/M output (illustrative) → $39.6k + $16.5k ≈ **$56k/month**. If 4k of the 6k input tokens are a stable system prompt + tools, cached at ~90% off → input cost drops by ~60%, to about $16k → total ≈ **$33k**. Routing 50% of easy queries to a model ~5× cheaper → about **$20k**. Lesson: caching, routing and output length dominate GenAI cost. Always do this arithmetic out loud.

</details>

<details><summary><b>Q3. How do you prevent a team from sending EU customer data to US regions via Bedrock?</b></summary>

SCP on the EU workload OU: deny `bedrock:InvokeModel*`/`Converse*` unless the resource is an `eu.` inference profile or an EU-region foundation model; deny `global.*` profiles. Keep Knowledge Bases, logs and vector stores in EU regions. Enforce VPC endpoint usage. Detect with Config/CloudTrail queries for non-EU profile ARNs.

</details>
