# AWS Deep-Dive Study Guide: From Zero to Senior-Interview Ready

A GitHub-style study guide for a **software engineer moving into cloud/DevOps/SRE and solutions architecture roles**. It covers how AWS works internally, why it's built that way, how it fails, and how to keep systems running. It includes real architecture deep dives (Netflix, AWS's own outages), a GenAI/Bedrock/AgentCore track for agentic engineers, and a map to the **SAP-C02** and **DOP-C02** Professional certifications.

> **Writing rule for every chapter:** *"If I were hiring an AWS engineer with 10 years of experience, what would I ask?"* Each chapter opens with that **Interviewer lens** and closes with **model answers**. No "what is the cloud" chapters.

---

## 🗺️ Learning path

```
Weeks 1–6   01-foundations ───────── how each core service works inside
Weeks 7–9   02-operating-aws ──────── observability, deploys, security, resilience, cost
Weeks 10–12 03-architecture-deep-dives  Netflix + AWS outages + cells + reference designs
Weeks 13–14 04-genai-bedrock ───────── Bedrock, RAG, agents/AgentCore, GenAI platform
Ongoing     06-labs (do one per chapter) · 05-interview-prep (weekly mock answers)
Weeks 15+   07-certification ──────── SAP-C02 / DOP-C02
```

## 📚 Contents

### 01 · Foundations: how it works under the hood

| # | Chapter | Key ideas |
|---|---|---|
| 01 | [Global infrastructure](01-foundations/01-global-infrastructure.md) | Regions/AZs, **control vs data plane**, **static stability**, cells, edge |
| 02 | [IAM deep dive](01-foundations/02-iam-deep-dive.md) | SigV4, policy evaluation, SCP/RCP/boundaries, STS, confused deputy, IRSA/Pod Identity, data perimeter |
| 03 | [VPC & networking](01-foundations/03-vpc-networking.md) | Nitro SDN, conntrack, SG vs NACL, TGW/Cloud WAN/PrivateLink/Lattice, hybrid DNS, data-transfer costs |
| 04 | [Compute & load balancing](01-foundations/04-compute-and-load-balancing.md) | Nitro, ASG levers, ALB error codes, ECS vs EKS, Lambda/Firecracker internals |
| 05 | [Storage](01-foundations/05-storage.md) | S3 consistency/perf/conditional writes, EBS internals, lazy-loaded snapshots, EFS/FSx |
| 06 | [Databases](01-foundations/06-databases.md) | Aurora quorum storage, DynamoDB partitions & single-table design, global tables, caching |
| 07 | [Messaging & events](01-foundations/07-messaging-and-events.md) | SQS semantics, idempotency, outbox, Kinesis/MSK, Step Functions, draining backlogs |

### 02 · Operating AWS: keeping it working

| # | Chapter | Key ideas |
|---|---|---|
| 01 | [Observability](02-operating-aws/01-observability.md) | SLO burn-rate alarms, EMF, cardinality, CloudTrail/Config |
| 02 | [IaC & safe deployments](02-operating-aws/02-iac-and-deployments.md) | CFN/CDK/Terraform at scale, AWS-style wave deployments, canaries, rollback safety |
| 03 | [Security & governance](02-operating-aws/03-security-and-governance.md) | Landing zone, SCPs, KMS envelope encryption, secrets rotation, incident response |
| 04 | [Resilience & DR](02-operating-aws/04-resilience-and-dr.md) | DR tiers, retries/backoff/jitter, metastable failures, load shedding, chaos, active-active |
| 05 | [Cost engineering](02-operating-aws/05-cost-engineering.md) | CUR investigations, Savings Plans strategy, architectural cost levers |

### 03 · Architecture deep dives & case studies

| # | Deep dive | Source |
|---|---|---|
| 01 | [Netflix: four-region active-active & region evacuation](03-architecture-deep-dives/01-netflix-multi-region-active-active.md) | Netflix Tech Blog, talks |
| 02 | [Netflix: Zuul, adaptive concurrency, prioritized load shedding](03-architecture-deep-dives/02-netflix-traffic-and-load-shedding.md) | Netflix Tech Blog 2016–2024 |
| 03 | [Netflix: EVCache, KV/TimeSeries/Counter abstractions, Keystone](03-architecture-deep-dives/03-netflix-data-platform.md) | Netflix Tech Blog 2013–2024 |
| 04 | [Netflix: Spinnaker/Kayenta, ChAP chaos, cloud security](03-architecture-deep-dives/04-netflix-delivery-chaos-and-security.md) | Netflix Tech Blog |
| 05 | [Cell-based architecture & shuffle sharding](03-architecture-deep-dives/05-cells-and-shuffle-sharding.md) | AWS Builders' Library / whitepapers |
| 06 | [AWS outage post-mortems: 2017, 2020, 2021, 2025](03-architecture-deep-dives/06-aws-outage-postmortems.md) | AWS Post-Event Summaries |
| 07 | [Reference architectures: flash sale, multi-tenant SaaS, video pipeline](03-architecture-deep-dives/07-reference-architectures.md) | Composite |

### 04 · GenAI on AWS (Bedrock & agents)

| # | Chapter |
|---|---|
| 01 | [Bedrock internals: request path, quotas, CRIS, prompt caching, Guardrails](04-genai-bedrock/01-bedrock-internals.md) |
| 02 | [RAG & Knowledge Bases: chunking, vector stores, ACL-aware retrieval, evals](04-genai-bedrock/02-rag-and-knowledge-bases.md) |
| 03 | [Agents & AgentCore: Runtime, Memory, Gateway, Identity, Policy, Evaluations](04-genai-bedrock/03-agents-and-agentcore.md) |
| 04 | [Production GenAI platform: AI gateway, chargeback, resilience](04-genai-bedrock/04-production-genai-platform.md) |

### 05 · Interview prep · 06 · Labs · 07 · Certification
- [Senior question bank (77 questions, linked to answers)](05-interview-prep/01-senior-question-bank.md)
- [Troubleshooting drills (10 scenarios)](05-interview-prep/02-troubleshooting-drills.md)
- [Hands-on labs (15 labs + capstone)](06-labs/README.md)
- [SAP-C02 / DOP-C02 certification map](07-certification/README.md)

---

## 🧠 The 10 ideas that show up everywhere
1. **Control plane vs data plane.** Recover using data planes only.
2. **Static stability.** Pre-provision; don't depend on changes during failure.
3. **Blast radius.** AZ → cell → region → waves; shuffle sharding.
4. **Everything retries.** Backoff + jitter + budgets, or you DDoS yourself.
5. **Idempotency.** At-least-once is the norm; make repeats harmless.
6. **Overload is normal.** Shed by priority; fail fast; bounded queues.
7. **Recovery is a workload.** Cold caches, lease storms, backlogs. Practise it.
8. **Least privilege as code.** SCP/RCP guardrails, boundaries, data perimeter, Access Analyzer.
9. **Measure what users feel.** SLOs, business KPIs (Netflix SPS), burn-rate alerts.
10. **Cost is architecture.** Data transfer, NAT, logs and storage tiers are design decisions.

## 📝 How to study each chapter
1. Read the **Interviewer lens** and try to answer first (you'll fail, that's the point).
2. Read the chapter; draw every diagram yourself from memory.
3. Do the matching **lab**.
4. Answer the **model questions out loud**, then compare.
5. Add your own notes/war stories at the bottom of the chapter (fork this repo).

## ⚠️ Accuracy note
AWS changes constantly (limits, prices, features, model IDs). Numbers here are accurate to the best of my knowledge as of **late 2026** and are labelled where approximate. Always confirm against the official AWS docs before quoting them in production or an exam. Netflix case studies summarize Netflix's public Tech Blog posts and talks (each chapter lists its sources). They describe Netflix's architecture *at the time of writing* of each post.
