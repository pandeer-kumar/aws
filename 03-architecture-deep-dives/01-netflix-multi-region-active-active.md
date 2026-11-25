# Deep Dive 01 · Netflix: From One Data Center to Four-Region Active-Active on AWS

> **Interviewer lens (questions this case study prepares you for):**
> - "Design a globally available streaming control plane that survives the loss of an entire AWS region."
> - "Why did Netflix choose active-active over active-passive? What did it cost?"
> - "How do you evacuate a region in minutes without overloading the others?"
> - "How do you keep user state consistent across regions with async replication?"
> - "What's the difference between the *control plane* and the *data plane* of Netflix, and why does only one of them live in AWS?"

> **Sources.** This chapter is built from Netflix Tech Blog posts and Netflix conference talks (titles in *Further reading*), cross-checked with secondary coverage. Numbers are as Netflix reported them at the time. Treat them as historical snapshots, not current specs.

---

## 1. Timeline: why each step happened

| Year | Event | Lesson that drove the next step |
|---|---|---|
| 2008 | A major database corruption in Netflix's own data center stopped DVD shipments for about 3 days | Vertically scaled, single points of failure don't work. Move to horizontally scalable, distributed systems in the cloud |
| 2009–2016 | Gradual migration to AWS, service by service (not lift-and-shift). Billing and customer data were among the last pieces. Migration was declared complete in early **2016** | Re-architect into microservices; denormalize; use NoSQL (Cassandra) for availability |
| 2011 | AWS us-east-1 **EBS outage**. Netflix stayed mostly up | Avoid EBS for critical paths; design for AZ failure; Chaos Monkey (2011) |
| 2012 (Christmas Eve) | AWS **ELB** control-plane problem in us-east-1 broke Netflix streaming on many devices for hours | One region isn't enough. Need **multi-region** |
| 2013 | **Active-active** across us-east-1 and us-west-2 | Users can be served from any region |
| 2016 | Added eu-west-1 (3 regions); "Chaos Kong" region-failure exercises | Regular evacuations |
| 2018 | **Project Nimble**: region evacuation in **< 10 minutes** (talks cite ~7 min) vs much longer before | Pre-provisioned capacity + progressive traffic shift |
| 2020s | **Four-region full-active** streaming control plane (per Netflix's SREcon 2025 talk) | Smaller "N" headroom, more flexibility to shape traffic |

---

## 2. Big picture: control plane vs data plane (Netflix's version)

```
          ┌────────────────────────── AWS (control plane) ──────────────────────────┐
 Device ──┤ Route 53 / geo-DNS ──► ELB/NLB ──► Zuul (edge gateway) ──► API layer    │
 (TV,     │                                          │                (GraphQL fed.) │
 phone)   │        microservices (thousands): auth, profiles, recommendations,      │
          │        playback/licensing (DRM), steering, billing, A/B testing …       │
          │        data: Cassandra, EVCache, KV abstraction, Kafka/Keystone, S3      │
          └────────────────────────────────────────┬─────────────────────────────────┘
                                                   │ "play" → steering picks best OCAs
                                                   ▼
          ┌──────── Open Connect (data plane: video bytes, NOT in AWS) ──────────────┐
          │ Open Connect Appliances (OCAs) inside ISPs + at IXPs; filled off-peak     │
          └───────────────────────────────────────────────────────────────────────────┘
```
- **Everything before you press play** (browse, search, recommendations, auth, license, which OCA to use) runs in AWS.
- **The video bytes** come from Netflix's own CDN, **Open Connect**, deployed inside ISPs. That's why a regional AWS outage doesn't stop video that's already streaming, but can stop *new* playback starts.
- **Key business metric: SPS (Stream Starts Per Second).** It follows a predictable daily curve. A deviation from it is the primary "something is wrong" signal, more reliable than any CPU graph.

---

## 3. Active-active: how it works

### 3.1 Routing
- Devices resolve Netflix endpoints via DNS (geo/latency-based) to a "home" region.
- **Zuul** (edge gateway) in each region can **proxy requests to another region**. This is the key lever. To move traffic you don't need to wait for DNS TTLs; Zuul forwards a configurable percentage of traffic cross-region. DNS is updated last, to make the move permanent for clients.

### 3.2 Data replication
| Data | Store | Cross-region mechanism | Consistency |
|---|---|---|---|
| Member/user state, viewing history, bookmarks | **Cassandra** | Native multi-datacenter async replication; writes at `LOCAL_QUORUM` | Eventual across regions; strong-ish within region |
| Hot cached data | **EVCache** (memcached-based) | Writes replicated to other regions by a Kafka-based replication pipeline | Eventual; acceptable for a cache |
| Configuration | Archaius / dynamic properties | Replicated | |
| Analytics events | Kafka / Keystone → S3 | Per-region pipelines | |

**Why it's acceptable:** most Netflix data tolerates eventual consistency (a bookmark position or rating a few hundred ms late is fine). The design avoids cross-region *synchronous* calls in the request path. Each region is **self-sufficient**.

### 3.3 What they explicitly had to build/fix (from the 2013 active-active posts and talks)
- **Isthmus** (an intermediate step): ELB tunnelling from one region to another to survive ELB failures.
- Cassandra: validate that multi-region replication keeps up under a full-load shift; write-heavy benchmarking (Netflix published "1 million writes/sec" Cassandra benchmarks on AWS in 2011).
- Services that assumed a single region (e.g. scheduled jobs, singletons, global locks) had to become region-aware.
- **Region-aware dependencies**: no service may call a dependency in another region on the hot path.

---

## 4. Region evacuation (Nimble and after)

### The problem
To evacuate region A to regions B and C, they must absorb A's traffic **instantly**. Earlier evacuations took a long time mostly because savior regions had to **scale up first** (autoscaling, instance launch, JVM warm-up, cache warm-up).

### The approach (as Netflix described it in 2018 talks/posts)
```
1. Decide to evacuate (sick region detected via SPS deviation / human call).
2. Ensure savior regions have capacity:
     keep enough capacity in every region to withstand any single-region failure
     (pre-provisioned, not scale-on-demand).
3. Zuul in the sick region starts PROXYING a trickle of traffic to healthy regions,
   ramping progressively to 100%. Watch SPS and error rates at each step.
4. Update DNS to point the sick region's clients at healthy regions (permanent move).
5. Fix, then fail back the same way.
Result reported in 2018: all users (100M+ at the time) shifted in under ~7 minutes.
```

### Capacity math (interviewers love this)
With **N** active regions each at load `L`, losing one means the remaining `N-1` must carry `N·L`:
- N = 2 → each needs **2×** normal peak capacity (100% headroom).
- N = 3 → **1.5×** (50% headroom).
- N = 4 → **1.33×** (33% headroom).

This is one reason to go from 3 to 4 regions: **headroom cost per region drops**. You also get more freedom to choose *where* to send traffic (latency, capacity), at the cost of more replication and operational surface.

### The hidden danger: cold caches and data stores
Shifting 33–50% more traffic into a region means its **caches don't hold the evacuated users' data**, so the misses hit Cassandra. Netflix's mitigations:
- EVCache cross-region replication keeps caches warm with other regions' writes.
- Data stores sized for the post-evacuation load (InfoQ coverage of Netflix's stateful-systems talks notes data stores must absorb a ~33% jump in traffic almost instantly).
- Progressive ramp so autoscaling and caches can follow.

---

## 5. Chaos at region scale: Chaos Kong
- Regularly (historically about monthly) **evacuate a region in production** as an exercise.
- Because evacuations are routine, the real one (an actual AWS regional event) is a known, practised procedure.
- Netflix has said that in major AWS us-east-1 events it evacuated and stayed up while many companies went down. The tooling was practised, not theoretical.

---

## 6. Trade-offs and challenges (what a senior should say)

| Challenge | Detail | Netflix's approach |
|---|---|---|
| **Cost** | Pre-provisioned headroom in every region + cross-region replication data transfer | Accept it for Tier-0 (streaming starts); 4 regions reduce headroom % |
| **Consistency** | Async replication → conflicts and stale reads | Data model tolerates eventual consistency; avoid cross-region sync calls |
| **Complexity** | Every service must be region-independent | Paved-road platform libraries (IPC, discovery, config) make it the default |
| **Testing** | Untested failover = no failover | Chaos Kong, Nimble drills, ChAP experiments |
| **Grey failures** | Region "half sick" is harder than "down" | SPS-based detection plus human judgment to evacuate early |
| **Thundering herd on failover** | Retries + cold caches + sudden load | Progressive ramp, load shedding by priority (deep dive 02) |

---

## 7. How you'd build this on AWS today (modern equivalents)

| Netflix built | Modern AWS-managed option |
|---|---|
| Zuul cross-region proxying + DNS change | **ARC routing controls** / Route 53 health-check failover; Global Accelerator traffic dials (percentage per region) |
| Cassandra multi-DC | **DynamoDB global tables** (MREC or MRSC), Keyspaces multi-region, Aurora Global Database / **Aurora DSQL** |
| EVCache replication | ElastiCache **Global Datastore** (Valkey/Redis), or app-level replication |
| Chaos Kong | **FIS** + ARC Region switch drills |
| SPS metric | CloudWatch business metric + anomaly detection + composite alarms |

### Reference design for an interview whiteboard
```
              Route 53 (latency) + ARC routing controls (per-region on/off)
                 │                │                 │
      ┌──────────▼───┐   ┌────────▼─────┐   ┌───────▼──────┐
      │  Region A    │   │  Region B    │   │  Region C    │   each at ≤ 66% peak (N=3)
      │ CloudFront → │   │              │   │              │
      │ ALB → EKS    │   │  same stack  │   │  same stack  │
      │ ElastiCache  │   │              │   │              │
      └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
             └────── DynamoDB global tables (user-homed writes) ──────┘
                     S3 CRR for assets · Kafka/MSK per region → S3
```
- **User homing**: each user writes in a home region (a stored attribute). Reads can go anywhere. This removes most write conflicts.
- **Evacuation runbook**: ARC Region switch plan → verify savior capacity (readiness checks) → shift 10% → 50% → 100% (Global Accelerator traffic dials or weighted records) → watch business metric → done.

---

## 8. Interview drill

<details><summary><b>Q: "Why not active-passive? It's cheaper."</b></summary>
Passive regions rot: config drift, untested capacity, cold caches, and the failover itself is a rare, scary event. Active-active proves every day that each region can serve traffic. The extra cost is the headroom, which is 1/(N-1) of capacity. For a business where minutes of downtime are front-page news and lost revenue, it's worth it. For lower tiers, warm standby is fine. Say that explicitly: **match the strategy to the tier.**
</details>

<details><summary><b>Q: "A region is degraded, not down (10% errors). Do you evacuate?"</b></summary>
If the business metric (SPS) shows real customer impact and the cause isn't quickly fixable, yes. Evacuation is a well-practised, low-risk operation at Netflix, so the bar to use it is low. That's the point of practising. The danger is waiting for certainty during a grey failure. Pull traffic early, debug later.
</details>

<details><summary><b>Q: "What would break first if you shifted 100% of traffic to one region?"</b></summary>
Stateful tiers: cache hit rate (cold keys), database read/write capacity, connection limits, and per-account/region AWS service quotas (ELB, Lambda concurrency, DynamoDB table limits). Then third-party dependencies with regional rate limits. Validate quotas in advance (Service Quotas + ARC readiness checks).
</details>

---

## Further reading (Netflix Tech Blog & talks)
- *Active-Active for Multi-Regional Resiliency* (Netflix Tech Blog, 2013).
- *Project Nimble: Region Evacuation Reimagined* (Netflix Tech Blog, 2018) and *How Netflix does failovers in 7 minutes flat* (opensource.com / PyCon 2018).
- *Completing the Netflix Cloud Migration* (Netflix, 2016).
- *Lessons Netflix Learned from the AWS Outage* (2011) and *A Closer Look at the Christmas Eve Outage* (2012).
- Ruslan Meshenberg, re:Invent ARC305 *How Netflix leverages multiple regions to increase availability* (2013).
- Joseph Lynch, SREcon25 Americas *Techniques Netflix Uses to Weather Significant Demand Shifts* (four-region full-active).
- AWS re:Invent 2022 *Netflix: Build resilient multi-site workloads* (AWS customer story).
