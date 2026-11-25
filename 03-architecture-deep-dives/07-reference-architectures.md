# Deep Dive 07 · Reference Architectures with Real Challenges

Three whiteboard-grade designs that come up in senior AWS interviews. Each lists **requirements → design → the hard parts → what breaks → how you keep it working**.

---

## A. Flash-sale e-commerce (100× traffic spike in 60 seconds)

### Requirements
- 2M users arrive at 09:00:00 for 50k discounted items. No overselling. Checkout p99 < 1s. The site must not go down for the people who don't get an item.

### Design
```
Users ─► CloudFront (static, cached product page; WAF rate/bot rules)
       ─► Virtual waiting room (CloudFront + Lambda@Edge/API GW + DynamoDB token bucket)
            admits N users/sec based on backend capacity
       ─► ALB ─► Checkout service (ECS/EKS, pre-scaled the night before; scheduled scaling)
             ├─► Inventory reservation: DynamoDB conditional write
             │      UpdateItem stock = stock - 1  IF stock > 0   (atomic, no oversell)
             │      write-sharded hot items: item#123#shard-0..19, each holds 1/20 of stock
             ├─► Order created (PENDING, TTL 10 min) → SQS → payment workers
             └─► Payment (idempotency key = orderId) → Step Functions saga
                     success → CONFIRMED · failure/timeout → compensate: stock + 1
```
### Hard parts
| Problem | Solution |
|---|---|
| Hot key: one item × 2M requests | Write sharding of stock across N items; pick a random shard, fall back to others when one is at 0 |
| Overselling | Conditional writes (no read-modify-write in app code) |
| Backend can't scale in 60 s | **Pre-scale** (scheduled), DynamoDB **warm throughput** / provisioned capacity, the waiting room admits only what you can serve |
| Payment provider rate limits | SQS buffer + concurrency-capped workers |
| Abandoned carts hold stock | Reservation TTL + compensation via Step Functions / DynamoDB TTL + Streams |
| Bots | WAF Bot Control, CAPTCHA at the waiting room, per-account purchase limits |

---

## B. Multi-tenant B2B SaaS (5,000 tenants, a few very large)

### Design choices: tenancy models
| Model | Isolation | Cost | Ops |
|---|---|---|---|
| **Silo** (account/stack per tenant) | Strongest | Highest | Many deployments |
| **Pool** (shared everything, `tenant_id` column) | Weakest (logic only) | Lowest | Easy |
| **Bridge** (shared compute, separate DB/schema per tenant) | Medium | Medium | Medium |
| **Cells** (pool within a cell, many cells) | Bounded blast radius | Medium | Medium |

Typical answer: **tiered**. Free/standard tenants pooled across cells; enterprise tenants in dedicated cells/accounts.

### Tenant isolation in a pool
- JWT carries `tenant_id` (Cognito / your IdP) → the service assumes a role with **session tags** (`tenant=acme`) → IAM policies with `${aws:PrincipalTag/tenant}` restrict DynamoDB `dynamodb:LeadingKeys` and S3 prefixes. Even a bug in app code can't read another tenant's data.
- Postgres: **row-level security** with `SET app.tenant_id`.
- Per-tenant KMS keys (or encryption context) for enterprise.

### Noisy neighbours
Per-tenant throttles (API Gateway usage plans), fair queues in SQS, shuffle-sharded workers, metered quotas. Per-tenant cost attribution via tagged metrics → CUR-joined unit economics.

### Onboarding/offboarding automation
Step Functions: create tenant record → assign cell → provision silo resources if needed (CDK pipeline) → IdP config → welcome. Offboarding: export, then crypto-shred (delete the tenant's KMS key).

---

## C. Video processing pipeline (inspired by Netflix's encoding platform)

Netflix described rebuilding its video processing pipeline as microservices on its **Cosmos** platform (2024 Tech Blog): workflow-driven, with each step (inspection, encoding, packaging, DRM) a separate service. Encoding is split into **chunks** processed in parallel across a large fleet, and they use spare/unused reserved capacity for encoding ("internal spot market" ideas they've blogged about).

### AWS design
```
Studio upload ─► S3 (multipart, Transfer Acceleration) ─► EventBridge "object created"
   ─► Step Functions workflow
        1. Inspect (MediaInfo in Lambda/Fargate) → validate
        2. Split into N chunks (by GOP/scene)  → S3
        3. Distributed Map: encode each chunk × each ladder rung (Batch on Spot / EKS + Karpenter Spot)
        4. Assemble + quality check (VMAF) → retry failed chunks only
        5. Package (HLS/DASH, CMAF) + DRM (SPEKE) → S3
        6. Publish metadata (DynamoDB) → CDN warm (CloudFront / origin shield)
```
### Hard parts
- **Spot interruptions:** chunking makes each task short (minutes), so you lose little work. Checkpoint to S3. Diversify instance types.
- **Cost:** encoding dominates. Graviton/`c7g`, Spot, right ladder per title (Netflix pioneered *per-title* and *per-shot* encoding optimization).
- **Throughput limits:** S3 prefix fan-out, Step Functions Distributed Map concurrency, Batch compute environment limits.
- **Idempotency:** each chunk output path is deterministic (`title/rung/chunk-0042.mp4`), so a retry overwrites instead of duplicating.
- **Observability:** per-title workflow status; per-stage durations; alarm on stuck executions.

---

## Interview tip
For any design: start with **requirements and numbers** (QPS, data size, latency, consistency, RTO/RPO). Then draw the happy path, then *say what breaks* (hot keys, retries, capacity, dependencies, regional failure) and how you'd detect and mitigate each. That "what breaks" section is what distinguishes 10-year engineers.
