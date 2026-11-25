# Deep Dive 03 · Netflix Data Platform: EVCache, Data Abstraction Layers, Counters, Keystone

> **Interviewer lens:**
> - "Hundreds of teams use Cassandra directly and each misuses it differently. How do you fix that organizationally *and* technically?"
> - "Design a distributed counter for 'views per title' at millions of increments/sec. Exact or approximate? Why?"
> - "How do you replicate a cache across regions, and why would you?"
> - "Design an event pipeline that ingests trillions of events/day into S3 and real-time consumers."
> - "Idempotency on a key-value store: how do you make retries safe?"

> **Sources:** Netflix Tech Blog posts listed at the bottom (2016–2024) plus talks. Details are as Netflix published them. I verified them through search summaries, not full-text reads, so check the original posts for exact numbers.

---

## 1. EVCache: caching as a first-class distributed system

**What:** "Ephemeral Volatile Cache". A memcached-based distributed cache with a Netflix client library, deployed **per AZ** (each AZ has a full copy), replicated **across regions**.

```
App (EVCache client)
  write → ALL AZ replicas in local region (async, fire-and-forget)  + emit to replication queue
  read  → local-AZ replica first; on miss/failure → another AZ's replica
Cross-region replication:
  client → Kafka topic (metadata: key, TTL) → Replication Reader → Replication Writer
         → remote region EVCache (reader may re-read the value locally to fetch the payload)
```
Why each design choice:

| Choice | Reason |
|---|---|
| Full copy per AZ | AZ failure loses no cache hit rate; reads stay in-AZ (lower latency, no cross-AZ cost) |
| Async writes | Cache must never slow down the request path |
| **Cross-region replication** | When a region is evacuated, the savior region's cache already holds the evacuated users' data, so no cold-cache stampede on Cassandra (see deep dive 01) |
| Kafka for replication | Durable buffer; replay; decouples regions |
| Later: memcached + **RocksDB on SSD** (`Moneta`/EVCache extstore) | Keep the hot set in RAM, the long tail on SSD. Much cheaper per GB at similar latency for cache workloads |

AWS equivalent: ElastiCache (Valkey/Redis) with replicas across AZs + **Global Datastore** for cross-region replication (one primary region, read replicas elsewhere). ElastiCache doesn't do multi-writer, so Netflix-style multi-region write replication needs app-level logic.

---

## 2. Data Abstraction Layers (DAL) — "databases as a platform"

### Problem
Hundreds of microservices talk to Cassandra, EVCache, DynamoDB, Elasticsearch directly. Each team learns (and mis-learns) data modelling, consistency levels, client tuning and migrations. Store migrations take years; outages happen from wide partitions, tombstones and hot keys.

### Solution: a **gRPC data gateway** with abstractions
```
Service ──gRPC──► Data Gateway (sidecar/agent + Data Gateway service per namespace)
                    ├── KV abstraction ───────► Cassandra / EVCache / DynamoDB / RocksDB
                    ├── TimeSeries abstraction ► Cassandra (+ Elasticsearch for index)
                    ├── Counter abstraction ───► TimeSeries + EVCache + rollup store
                    ├── Entity / Graph / WAL abstractions …
                 Namespaces configured declaratively: backing store, consistency, TTL, region, SLOs
```
Benefits: one place for best practices, transparent **store migration** (shadow writes, then switch reads), consistent observability, limits/back-pressure, and a stable API for app teams.

### 2.1 Key-Value abstraction (2024)
- **Data model**: a two-level map: `id (record) → sorted map of key → value`. That's `HashMap<String, SortedMap<Bytes, Bytes>>`, which maps to Cassandra `PRIMARY KEY (id, key)`.
- APIs: `PutItems`, `GetItems` (with predicates/ranges and pagination), `DeleteItems`, `MutateItems` (batch).
- **Idempotency tokens**: each mutation carries a token (client timestamp + random nonce). Retries and hedged requests don't double-apply, and last-writer-wins ordering is preserved.
- **Chunking**: large values (above about 1 MiB) are split into chunks stored separately. Only metadata stays in the main table, and the chunks and the commit are tied together by the same idempotency token.
- **Adaptive pagination**: page by *bytes*, not item count, to keep latency predictable.
- **Deletes designed to minimize tombstones** (e.g. one record-level or range tombstone rather than one per item). Tombstone build-up is a classic Cassandra latency killer.
- **Signals** from server to client (e.g. compression on/off, page size) let the platform tune clients without redeploys.

**Interview takeaway:** the abstraction lets the platform team encode years of Cassandra operational knowledge once. That's the senior answer to "100 teams misuse the database".

### 2.2 TimeSeries abstraction (2024)
- For high-volume, immutable, time-ordered events (viewing events, logs, audits).
- Writes partitioned by **time buckets** *and* event-ID buckets to avoid wide partitions; tables per time window (easy TTL and deletion by dropping old tables).
- Idempotent writes; reads by time range; optional indexing for search.

### 2.3 Distributed Counter abstraction (2024)
Different counters need different trade-offs, so Netflix offers **modes**:

| Mode | Mechanism | Accuracy | Latency | Use |
|---|---|---|---|---|
| **Best-effort regional** | EVCache increments (atomic on one cache node, chosen by consistent hashing) | Approximate; lost on cache failure; no global view | lowest | A/B test impressions, rate-ish metrics |
| **Eventually consistent global** | Each increment stored as an **immutable event** in TimeSeries (idempotent); background **rollup** aggregates into a counter store; reads return the last rollup (+ optional cache) | Exact eventually | low read, delayed accuracy | Durable counts where seconds of lag are fine |
| **Accurate global** (experimental) | Last rollup + on-the-fly aggregation of events since then | Closer to real-time exact | higher | Billing-like needs |

Why not just `UPDATE counter = counter + 1` in Cassandra? Cassandra counters aren't idempotent (a retry double-counts), and hot counters create hot partitions. **Event log + rollup** gives idempotency, auditability, replay and scale.

AWS equivalent design: DynamoDB `UpdateItem ADD` with an idempotency guard for modest scale; at high scale, **Kinesis/MSK → aggregation (Flink/Lambda) → DynamoDB**, with write-sharded counter items (`views#title#shard-0..N`) summed on read.

---

## 3. Keystone: real-time stream processing platform

```
Apps (thousands) ──► Keystone client/proxy ──► Fronting Kafka clusters (per region)
                                                   │
                                     Keystone router (Flink jobs; per-stream)
                              ┌────────────────────┼──────────────────────┐
                              ▼                    ▼                      ▼
                         S3 (Hive/Iceberg)   Elasticsearch          Consumer Kafka (for real-time
                                                                     stream processing: Flink/Mantis)
Control plane: self-service UI → declarative pipeline config → reconciler deploys Flink jobs
```
- Scale: Netflix described Keystone in 2018 as handling on the order of **a trillion+ events per day** and petabytes of data. It has grown since.
- **Self-service**: teams declare "stream X with filter Y to sink Z". The platform provisions and operates the Flink jobs.
- **Isolation**: each stream/sink gets its own job, so one bad consumer doesn't block others (cells again).
- Trade-offs: at-least-once delivery (dedupe downstream); fronting Kafka sized for bursts; message loss acceptable for some telemetry ("best effort, high availability" vs "lossless" streams configured per stream).

AWS-native equivalent: producers → **Kinesis Data Streams** or **MSK** → **Managed Service for Apache Flink** → **Firehose** to S3 (Iceberg tables via S3 Tables) / OpenSearch. Use per-stream isolation and a self-service config layer (e.g. a CDK construct catalogue).

---

## 4. Lessons a senior engineer should take from Netflix's data platform
1. **Abstract the store, not just the client.** A gateway with namespaces enables migrations and enforces best practices.
2. **Idempotency everywhere.** Tokens on every mutation make retries, hedging and replication safe.
3. **Pick consistency per use case.** Offer modes (best-effort / eventual / accurate) instead of one-size-fits-all.
4. **Design against the store's pathologies** (wide partitions, tombstones, hot keys) in the abstraction layer.
5. **Caches are tier-0 infrastructure.** Replicate them for failover, warm them before shifting traffic.
6. **Event log + rollup** beats in-place counters at scale.

---

## 5. Interview drill

<details><summary><b>Q: "Design 'Continue Watching' (bookmarks) for 300M members across 4 regions."</b></summary>

Write path: the device sends position heartbeats every N seconds → gateway → bookmark service → KV store (`id=member#profile`, `key=title`, value=position+timestamp) with idempotency token, LOCAL_QUORUM in the home region, async multi-region replication (Cassandra / DynamoDB global tables), last-writer-wins by client timestamp (positions are monotonic within a session). Cache recent reads in an EVCache-style cache replicated cross-region. Read path: home row from cache → KV. Heartbeats are high-volume: batch them and shed them first under load (priority BEST_EFFORT; the final "stop" event is DEGRADED/CRITICAL). Analytics: also emit to the event pipeline (Keystone/Kinesis) → S3 for recommendations.

</details>

<details><summary><b>Q: "Exact vs approximate counters: how do you decide?"</b></summary>

Ask what the count is used for. Billing, quotas and legal reporting need exact counts, durable and idempotent (event log + rollup, accept seconds of delay). Dashboards, popularity rankings and A/B impression counts tolerate approximations (in-memory/cache counters, HyperLogLog for uniques), which are far cheaper and lower-latency. Say the cost/latency/accuracy triangle out loud.

</details>

## Further reading (Netflix Tech Blog)
- *Announcing EVCache: Distributed in-memory datastore for Cloud* (2013); *Caching for a Global Netflix* (2016); *Evolution of Application Data Caching: From RAM to SSD* (2018).
- *Introducing Netflix's Key-Value Data Abstraction Layer* (Sept 2024).
- *Introducing Netflix TimeSeries Data Abstraction Layer* (Oct 2024).
- *Netflix's Distributed Counter Abstraction* (Nov 2024); InfoQ coverage Dec 2024.
- *Keystone Real-time Stream Processing Platform* (2018); *Evolution of the Netflix Data Pipeline* (2016).
- QCon/InfoQ: *How Netflix Uses Data Abstraction to Operate Services at Scale*.

<!-- nav -->
---

← [Deep Dive 02 · Netflix Traffic Management: Zuul, Adaptive Concurrency, Prioritized Load Shedding](02-netflix-traffic-and-load-shedding.md) · [🏠 Home](../README.md) · [Deep Dive 04 · Netflix Delivery, Chaos Engineering & Cloud Security](04-netflix-delivery-chaos-and-security.md) →
