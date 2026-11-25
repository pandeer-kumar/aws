# 04 · Resilience Engineering & Disaster Recovery

> **Interviewer lens.**
> - "Define RTO and RPO for our payments system and pick a DR strategy. Justify the cost."
> - "Timeouts, retries, backoff, jitter, circuit breakers: walk me through how they interact and how retries cause outages."
> - "What is a metastable failure? Give an AWS example."
> - "How do you run chaos engineering safely in production?"
> - "Multi-region active-active: what's actually hard about it?"

---

## 1. DR strategies (the four AWS patterns)

| Strategy | RPO | RTO | Cost | What runs in DR region |
|---|---|---|---|---|
| **Backup & restore** | hours | hours–day | $ | Nothing (backups copied cross-region via AWS Backup) |
| **Pilot light** | minutes | 10s of minutes | $$ | Data replicated (Aurora Global/DDB global tables/S3 CRR); compute off or minimal |
| **Warm standby** | seconds–minutes | minutes | $$$ | Scaled-down full stack, serving health checks / some traffic |
| **Multi-site active-active** | ~0–seconds | ~0–minutes | $$$$ | Full capacity, serving real traffic |

**Choose per workload tier, not per company.** Tier 0 (login, playback, payments) might be active-active; internal reporting might be backup & restore.

### What actually goes wrong in DR
- **Untested.** If you don't practise failover, it will not work. Netflix evacuates regions regularly as normal operations.
- **Control-plane dependence**: launching capacity at failure time when everyone else is too (capacity is not guaranteed). Pre-scale or reserve.
- **Hidden single-region dependencies**: IAM/Route 53 control planes in us-east-1, a third-party SaaS in one region, a config bucket, a CI system, a secrets store, the DNS for your own status page.
- **Data**: async replication means RPO > 0, and split-brain risk if both regions accept writes for the same entity.
- **Failing back** is often harder than failing over.

### AWS tools
- **Application Recovery Controller (ARC):** routing controls (data-plane on/off switches on a 5-region cluster), readiness checks, **zonal shift / zonal autoshift** (AWS shifts traffic away from an impaired AZ for you), and **Region switch** plans to orchestrate cross-region failover steps.
- **AWS Backup:** cross-account/cross-region copies, **logically air-gapped vaults**, Vault Lock (WORM), restore testing.
- **Elastic Disaster Recovery (DRS):** block-level replication of servers into a low-cost staging area (for lift-and-shift apps).
- **Resilience Hub:** assess apps against RTO/RPO targets.
- **Fault Injection Service (FIS):** managed chaos experiments (AZ power interruption scenario, API throttling, EKS pod faults, network disruption) with stop conditions tied to CloudWatch alarms.

---

## 2. Timeouts, retries, backoff and jitter

### Retry amplification
```
Client → API (3 retries) → Service A (3 retries) → Service B (3 retries) → DB
One DB blip ⇒ up to 4 × 4 × 4 = 64 attempts per original request. That's a self-inflicted DDoS.
```
Rules (from the Builders' Library and Netflix practice):
1. **Retry at one layer** (usually the one closest to the client that knows if retrying is safe), or use **retry budgets** / token buckets (AWS SDK "standard" and "adaptive" retry modes use a retry quota).
2. **Exponential backoff with full jitter**: `sleep = random(0, min(cap, base * 2^attempt))`. Jitter de-synchronizes clients, so they don't retry in lockstep waves.
3. **Timeouts on everything**, set from downstream latency percentiles (e.g. p99.9 + margin), and **deadline propagation** (pass the remaining time budget downstream; don't start work that can't finish).
4. **Only retry idempotent operations** (or ones with idempotency keys).
5. **Circuit breakers**: after N failures, fail fast for a cool-down period (Netflix Hystrix popularized this; today resilience4j or service mesh).
6. **Avoid fallback that's rarely exercised.** The Builders' Library argues fallback paths are untested and fail exactly when you need them. Prefer making the primary path more reliable, or use a fallback that is exercised constantly (e.g. **constant work**: always push full config rather than deltas).

### Metastable failures
A system enters a bad state that persists **even after the trigger is gone**, held there by a feedback loop (retries, cache misses, lease renewals).
- **AWS Oct 2025:** after DynamoDB DNS was fixed, EC2's droplet/lease management had accumulated such a backlog that it entered **congestive collapse**. Lease work timed out and was retried, adding more work. Recovery needed throttling and manual intervention.
- **AWS Dec 2021:** internal network congestion → retries from internal clients → more congestion; monitoring was impaired too.
- **Classic:** cache cluster restarts → all reads hit the DB → DB slows → requests time out → clients retry → DB never recovers.

Break the loop: **load shedding, admission control, throttling retries, and recovering in stages** (drain backlog at a controlled rate).

---

## 3. Load shedding and admission control

- **Prioritized load shedding**: when overloaded, reject low-priority work first (pre-fetch, telemetry, background) so critical work (start playback, checkout) succeeds. Netflix runs this at the Zuul gateway (2020) and inside individual services (2024). See [02-netflix-traffic-and-load-shedding.md](../03-architecture-deep-dives/02-netflix-traffic-and-load-shedding.md).
- **Adaptive concurrency limits** (Netflix `concurrency-limits` library): estimate the service's concurrency capacity from latency changes (TCP-congestion-control style: Vegas/gradient algorithms) and reject over the limit. No static thresholds to tune.
- **Fail fast > queue forever.** Bounded queues; reject with 429/503 + `Retry-After`.
- **API Gateway / ALB** throttles + **usage plans** per API key; WAF rate-based rules.

---

## 4. Chaos engineering

Principles (from Netflix's *Principles of Chaos*):
1. Define **steady state** with business metrics (Netflix: SPS, stream starts per second).
2. Hypothesize steady state continues in both control and experiment groups.
3. Introduce real-world events (instance death, latency, region failure).
4. Try to disprove the hypothesis. **Minimize blast radius**; automate; run continuously in production.

Netflix evolution: **Chaos Monkey** (kill instances, 2011) → **Simian Army** (Latency Monkey, Chaos Gorilla for AZs, Chaos Kong for regions) → **FIT** (Failure Injection Testing, request-scoped fault injection via headers) → **ChAP** (Chaos Automation Platform: routes a tiny % of real users to control vs experiment clusters, auto-stops on SPS deviation).

On AWS: **FIS** experiment templates with stop conditions; start in staging, then prod with small scope; game days for human processes (paging, runbooks, comms).

---

## 5. Active-active multi-region: what's hard

1. **Data consistency.** Async replication → conflicts. Strategies: **home region per user/tenant** (writes go to the home region; reads anywhere), CRDT-like data types, last-writer-wins where acceptable, or strongly consistent multi-region stores (DynamoDB MRSC, Aurora DSQL) at a latency cost.
2. **Routing.** GeoDNS/latency routing + the ability to steer *percentages* of traffic (Netflix uses Zuul proxies to move traffic progressively, then DNS).
3. **Capacity.** Each region must absorb the failed region's load: N regions → each needs `1 + 1/(N-1)` × normal peak.
4. **Cold caches** in the receiving region → DB stampede. Warm caches via replication (Netflix EVCache replicates writes cross-region).
5. **Testing.** Regular evacuations.
6. **Cost.** Duplicate infra + cross-region data transfer.

Full case study: [01-netflix-multi-region-active-active.md](../03-architecture-deep-dives/01-netflix-multi-region-active-active.md).

---

## 6. Senior interview questions — model answers

<details><summary><b>Q1. "We need 99.99%." What does that mean operationally?</b></summary>
52.6 minutes of downtime per year, about 4.3 min/month. You can't have humans in the detection-to-mitigation loop for every incident: paging plus diagnosis alone takes 15+ minutes. So you need automated detection and mitigation (auto rollback, zonal autoshift, health-check failover) and deployments that can't take down more than a fraction of capacity. Also, every hard dependency's availability multiplies: five dependencies at 99.99% each give ~99.95% if any one failing fails you. You need redundancy or graceful degradation per dependency, and **you can't exceed the availability of your critical dependencies** without isolating from them.
</details>

<details><summary><b>Q2. Design DR for a payments API: RPO ≤ 1s, RTO ≤ 5 min.</b></summary>
Warm standby or active/passive-hot in a second region. Aurora Global Database (lag typically < 1s; managed failover) or DynamoDB global tables. Stateless compute pre-scaled to handle full load (or ≥ 50% + fast scaling with reserved capacity). ARC routing controls for the traffic switch (data plane). Idempotency keys so that clients retrying across the failover don't double-charge. Runbook automation with ARC Region switch. Quarterly failover drills measuring actual RTO/RPO. Explicitly document that RPO is "lag at failure time" and alarm if lag exceeds 1s.
</details>

<details><summary><b>Q3. Your service depends on a flaky third-party API. Make it resilient.</b></summary>
Timeouts set from its latency SLO; bounded retries with jitter (idempotent calls only) under a retry budget; circuit breaker; bulkhead (separate thread/connection pool so it can't exhaust the main pool); queue requests asynchronously if the product allows (SQS + worker with rate limit); cache responses where valid; degrade gracefully in the UI. Monitor their SLA vs reality and keep the contract evidence.
</details>
