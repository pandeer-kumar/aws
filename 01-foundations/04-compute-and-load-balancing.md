# 04 · Compute & Load Balancing: EC2/Nitro, Auto Scaling, Containers, Lambda

> **Interviewer lens.**
> - "What is Nitro and why did it change EC2 performance and security?"
> - "Your ASG scales too late during traffic spikes. Walk me through every lever."
> - "ECS vs EKS vs Lambda vs Fargate for a new 40-service platform. Defend your choice."
> - "Explain a Lambda cold start end to end. How do SnapStart and provisioned concurrency differ?"
> - "ALB returns 502s during deployments. Root causes?"
> - "Spot interruption strategy for a stateful-ish batch platform?"

---

## 1. EC2 and the Nitro System

Before Nitro (around 2013–2017), EC2 ran the Xen hypervisor, which used host CPU for networking, storage and management. **Nitro** moves that work onto dedicated hardware:

| Nitro component | Job |
|---|---|
| **Nitro Cards** | VPC networking (ENA, encapsulation, SGs), EBS (NVMe over the network), instance storage, the management controller |
| **Nitro Security Chip** | Hardware root of trust; blocks writes to firmware from the host |
| **Nitro Hypervisor** | Thin KVM-based hypervisor that only does CPU/memory partitioning |
| **Nitro Enclaves** | Isolated VMs with no network/storage, for secrets processing (attestation with KMS) |

Why it matters:
- Nearly all host resources go to *your* instance, so performance is close to bare metal (`*.metal` instances exist).
- **No operator access.** There is no SSH/console path for AWS staff into the host memory of Nitro instances. This is part of AWS's confidentiality claims, and it is the basis for Nitro being independently audited.
- EBS and network are **network-attached via the Nitro card**. That's why EBS has bandwidth limits per instance type, not just per volume.

### Instance selection quick-reference

| Family | For | Note |
|---|---|---|
| `m` | General | Default starting point |
| `c` | CPU-bound (web, encoding) | |
| `r`, `x` | Memory (caches, in-memory DB) | |
| `i`, `d` | Local NVMe (Cassandra, Kafka, Elasticsearch) | **Instance store is wiped** on stop/terminate/host failure. Replicate! |
| `g`, `p`, `trn`, `inf` | GPU / Trainium / Inferentia | Capacity is scarce. Use **Capacity Blocks** / ODCRs for guaranteed GPU capacity |
| `t` | Burstable | CPU credits. "Unlimited mode" bills surplus. Do not use for steady load. |
| Suffix `g` (Graviton) | Arm64 | ~20–40% better price/perf for most workloads; needs arm64 builds |

### Purchasing

| Option | Discount | Commitment | Risk |
|---|---|---|---|
| On-Demand | 0 | None | — |
| **Savings Plans** (Compute / EC2 Instance) | up to ~66–72% | $/hr for 1 or 3 yr | Over-commitment |
| Reserved Instances | up to ~72% | Specific type/region | Less flexible; mostly superseded by SPs |
| **Spot** | up to ~90% | None | 2-min interruption notice (+ rebalance recommendation earlier) |
| ODCR / Capacity Blocks | 0 (capacity guarantee) | Yes | Pay even if idle |

**Savings Plans give a discount, not capacity.** Only ODCRs guarantee capacity. For DR in another region during a major event, everyone launches at once. That's why Netflix and others **pre-provision or reserve** capacity in the savior regions.

---

## 2. Auto Scaling, done right

```
            metrics ─► CloudWatch ─► scaling policy ─► ASG desired capacity ─► launch (control plane!)
                                                                 │
                                    warm pool (pre-initialized, stopped/hibernated instances)
```

| Policy | How | Use |
|---|---|---|
| **Target tracking** | Keep metric at target (e.g. CPU 50%, ALB `RequestCountPerTarget` 1000) | Default choice |
| Step scaling | Bands → step adjustments | Non-linear responses |
| **Predictive scaling** | ML forecast from 14 days of history; scales *ahead* | Daily/weekly cyclic traffic |
| Scheduled | Cron | Known events (sale at 9am) |

### "We scale too late": the levers
1. **Better metric.** Requests per target or queue depth per worker lead CPU. CPU lags.
2. **Faster signal.** Detailed monitoring (1-min) or high-resolution custom metrics.
3. **Faster boot.** Bake AMIs (EC2 Image Builder) instead of heavy user-data; set **instance warm-up / default instance warmup** correctly.
4. **Warm pools** cut launch to seconds.
5. **Predictive scaling** for cycles + **scheduled** for known events.
6. **Headroom.** Lower target utilisation (e.g. 40% instead of 70%). Costs money, buys time.
7. **Load shedding** for the gap. See the Netflix case study [03-architecture-deep-dives/02-netflix-traffic-and-load-shedding.md](../03-architecture-deep-dives/02-netflix-traffic-and-load-shedding.md).

### Lifecycle hooks and graceful shutdown
- `autoscaling:EC2_INSTANCE_TERMINATING` hook → drain, flush logs, deregister.
- ELB **deregistration delay** (default 300s) must be ≥ your longest in-flight request but not huge, or deploys crawl.
- Spot: listen to the IMDS `/spot/instance-action` endpoint or the EventBridge *EC2 Spot Instance Interruption Warning* event. Use **capacity-rebalance** and **price-capacity-optimized** allocation across many instance types (attribute-based instance selection).

---

## 3. Load balancers in depth

### ALB request path
Client → ALB node (one or more per AZ, IPs change, **always use DNS**) → listener rules (host/path/header/query) → target group → target.

**502/503/504 decoder:**

| Code | Generated by ALB because… | Typical fix |
|---|---|---|
| 502 | Target closed the connection / sent malformed response / **target's keep-alive timeout < ALB idle timeout** | Set app keep-alive (e.g. Node `server.keepAliveTimeout`) **greater than** ALB idle timeout (60s default) |
| 503 | No healthy targets registered in the target group | Health checks, deploy ordering |
| 504 | Target didn't respond within idle timeout | Slow backend; raise timeout or fix latency |
| 460 | Client closed before ALB responded | Client timeouts too short |
| 561 | IdP error (OIDC auth) | |

**502s during deploys** = targets killed before deregistration finished, or the app exits without draining. Fix: handle SIGTERM, stop accepting new connections, finish in-flight requests, and keep `deregistration_delay` aligned with your shutdown timeout.

### Algorithms and features
- Round robin (default), **least outstanding requests** (better for uneven request cost), **weighted random with automatic target weights (anomaly mitigation)**, which down-weights targets with outlier error rates.
- **Slow start** ramps new targets in gradually (JVM warm-up).
- **Weighted target groups** for blue/green or canary at the LB.

### NLB notes
- Flow-hash routing (5-tuple), sticky for the connection lifetime. Long-lived connections (gRPC, WebSocket) **won't rebalance** when you add targets. Clients must reconnect periodically.
- Client IP preservation can cause **hairpinning** failures when a target connects to itself through the NLB.

---

## 4. Containers: ECS vs EKS vs Fargate

| | ECS | EKS | Fargate (launch type for either) |
|---|---|---|---|
| Control plane | AWS-proprietary, free | Upstream Kubernetes, $0.10/hr/cluster (more on extended support) | — |
| Learning curve | Low | High | — |
| Ecosystem | AWS-native | Huge (Helm, operators, service mesh, Karpenter, Argo) | — |
| Node management | EC2 ASG / capacity providers / **ECS Managed Instances** | Managed node groups / **Karpenter** / **EKS Auto Mode** | None. Per-task microVM (Firecracker) |
| Upgrades | None for control plane | You must keep up with K8s versions (about 14 months standard support) | Platform version |
| Best for | Small/medium teams wanting simplicity | Multi-cloud skills, platform teams, complex scheduling | No-ops; pay a premium per vCPU |

**Senior answer for "40-service platform":** "If the org has no Kubernetes expertise and is all-in on AWS, ECS on Fargate gets them to production fastest with the least toil. If they need portability, the CNCF ecosystem, GPU scheduling or many custom controllers, EKS with Karpenter (or Auto Mode). The deciding factor is the operating model: who is on call for the platform?"

### EKS things that break in production
- **IP exhaustion.** The VPC CNI gives each pod a VPC IP. Fixes: prefix delegation (/28 per ENI slot), custom networking with a secondary CIDR (100.64.0.0/10), or IPv6 clusters.
- **CoreDNS** throttling / ndots:5 → 5× DNS queries. Use NodeLocal DNSCache and lower `ndots`.
- Version upgrades: deprecated APIs. Run `pluto`/`kubent` and EKS upgrade insights.
- Karpenter consolidation evicting pods. Set PodDisruptionBudgets and `karpenter.sh/do-not-disrupt`.

---

## 5. Lambda internals

```
Invoke ─► Frontend (auth, throttles) ─► Placement / Worker fleet
                                             └─ Firecracker microVM (per function version, never shared across accounts)
                                                  └─ Execution environment: runtime + your code
   COLD: download code/layers (or container image chunks, lazily loaded & cached)
         → start runtime → run INIT (your global code, 10s limit) → handler
   WARM: reuse env → handler only
```
- **Concurrency** = requests in flight. Default account limit 1,000 per region (raisable). Each environment handles **one request at a time** (except Lambda Managed Instances, which allow multiple).
- **Scaling rate:** each function can add 1,000 concurrent executions every 10 seconds.
- **Reserved concurrency** = cap *and* guarantee. **Provisioned concurrency** = pre-initialized envs (no cold start, costs money).
- **SnapStart** (Java, Python, .NET): snapshot the initialized microVM (Firecracker snapshot), restore it on cold start. Watch out for **uniqueness**: random seeds and connections captured in the snapshot. Use runtime hooks.
- **Invocation models:**
  - Sync (API GW, ALB): caller retries.
  - **Async** (S3, SNS, EventBridge): internal queue, 2 retries, then **on-failure destination / DLQ**. Max event age up to 6h.
  - **Poll-based / event source mapping** (SQS, Kinesis, DynamoDB Streams, Kafka): Lambda polls. For streams, **one failing record blocks the shard**. Use `BisectBatchOnFunctionError`, `MaximumRetryAttempts`, and on-failure destinations. For SQS, use `ReportBatchItemFailures`.
- Limits to remember: 15 min max, 10 GB memory (CPU scales with memory, ~1 vCPU per 1,769 MB), 10 GB `/tmp`, 6 MB sync payload (response streaming for larger), 256 KB async.

### Lambda in a VPC
Since 2019, Lambda uses **Hyperplane ENIs** shared per subnet+SG combination, created when the function is configured (not per invoke). Cold-start VPC penalty is mostly gone. Egress to the internet still needs a NAT.

---

## 6. Senior interview questions — model answers

<details><summary><b>Q1. Lambda vs containers for a 5k RPS steady API?</b></summary>

At steady high RPS, Lambda pricing (per request + GB-s) usually costs more than containers at good utilisation. Containers also allow connection pooling and in-process caching. Lambda wins for spiky, event-driven, or low-traffic workloads and for zero ops. Do the maths: 5,000 rps × 100ms × 1GB ≈ 500 GB-s/s ≈ $0.0083/s ≈ **$21.6k/month in compute plus ~$2.6k in requests**. A Fargate/EC2 fleet at 60% utilisation is typically a fraction of that. Mention Lambda Managed Instances (Lambda on EC2 you choose, with multi-concurrency) as the middle ground.

</details>

<details><summary><b>Q2. Kinesis-triggered Lambda is falling behind (IteratorAge rising). What do you do?</b></summary>

1) Check for errors. A poison record blocks the shard: enable bisect, set max retries/record age, and add an on-failure destination. 2) Increase **ParallelizationFactor** (up to 10 concurrent batches per shard, ordering kept per partition key). 3) Tune batch size/window. 4) Add shards (or on-demand mode). 5) Optimise the handler (downstream calls are usually the bottleneck; check whether the downstream DB is throttling). 6) For fan-out, use enhanced fan-out consumers (dedicated 2 MB/s per shard per consumer).

</details>

<details><summary><b>Q3. How do you make an ASG fleet statically stable across AZs?</b></summary>

Spread over 3 AZs with capacity so N-1 AZs can carry peak. Set minimum capacity to that level (not relying on scale-out during an event). Enable zonal shift/autoshift on the ALB. Keep per-AZ NAT and endpoints. Make sure the ASG's AZ rebalancing won't thrash during an event: suspend `AZRebalance` if needed, and know that ASG launches depend on the EC2 control plane.

</details>

<details><summary><b>Q4. Your Java service on EKS has p99 latency spikes every few minutes.</b></summary>

Hypotheses: GC pauses (check GC logs; container memory limits vs heap; use `-XX:MaxRAMPercentage`), **CPU throttling from CFS quotas** (`container_cpu_cfs_throttled_periods_total`; remove CPU limits or right-size them), noisy neighbours, DNS lookups (ndots), conntrack exhaustion on nodes, or the ALB/NLB idle reset pattern. Netflix has written about CPU isolation and noisy neighbours on its Titus container platform, a good real-world reference.

</details>

---

## Further reading
- re:Invent: "Powering next-gen EC2 instances: Deep dive into the Nitro System".
- Firecracker paper (NSDI '20): *Firecracker: Lightweight Virtualization for Serverless Applications*.
- AWS docs: *Troubleshoot your Application Load Balancers* (HTTP error codes).
- Netflix Tech Blog: Titus container platform and *Predictive CPU isolation of containers at Netflix*.

<!-- nav -->
---

← [03 · VPC & Networking: How Packets Actually Move in AWS](03-vpc-networking.md) · [🏠 Home](../README.md) · [05 · Storage: S3, EBS, EFS, FSx: How They Work and How They Fail](05-storage.md) →
