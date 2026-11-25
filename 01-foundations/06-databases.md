# 06 · Databases: RDS, Aurora, DynamoDB, ElastiCache: Internals and Failure Modes

> **Interviewer lens.**
> - "How is Aurora's storage different from RDS MySQL on EBS? Explain the 4-of-6 quorum and why it ships only redo log records."
> - "Aurora failover takes 30 seconds in your test but 2 minutes in prod. Why?"
> - "Design a DynamoDB single-table model for an order system. What's a hot partition and how do you fix one?"
> - "DynamoDB global tables vs Aurora Global Database: consistency, conflict resolution, RPO/RTO."
> - "Cache stampede took down your DB after a Redis failover. How do you prevent it?"

---

## 1. Choosing a database: the senior framing

Pick the database from **access patterns, consistency needs, scale curve and operating model**, not from habit.

| Need | First choice | Why |
|---|---|---|
| Relational, joins, moderate scale, existing SQL apps | **Aurora** (PostgreSQL/MySQL) | Managed HA, fast failover, readers |
| Key-value/document, predictable single-digit ms at any scale | **DynamoDB** | Serverless, partitioned, no connection limits |
| Distributed SQL, active-active multi-region, strong consistency | **Aurora DSQL** | Serverless distributed Postgres-compatible, optimistic concurrency |
| Sub-ms cache, rate limits, leaderboards, sessions | **ElastiCache (Valkey/Redis OSS/Memcached)** / **MemoryDB** (durable) | |
| Time series | Timestream (InfluxDB) | |
| Graph | Neptune | |
| Wide-column / Cassandra API | Keyspaces | |
| Search / vectors | OpenSearch; Aurora pgvector; S3 Vectors | |

---

## 2. RDS (classic engines on EBS)

- **Multi-AZ (instance)**: synchronous block-level replication to a standby in another AZ. Failover flips the DNS CNAME, typically **60–120s**. The standby serves no reads.
- **Multi-AZ DB cluster** (MySQL/PG): 1 writer + 2 readable standbys, semi-sync, failover **~35s**.
- **Read replicas**: async; can be cross-region; promotable (manual, breaks replication).
- **Backups**: daily snapshot + transaction logs every 5 min → PITR to any second within retention (≤35 days).
- **RDS Proxy**: connection pooling/multiplexing, keeps client connections open during failover (cuts failover impact by up to ~66%), IAM auth. Essential with Lambda.

---

## 3. Aurora: why it's different

```
   Writer instance ──redo log records only──►  Shared cluster volume (10 GB "protection groups")
   Reader instances (up to 15) ◄── read same storage, get log stream for cache invalidation
                                   ▼
          6 copies per protection group, 2 per AZ × 3 AZs
          Write quorum 4/6   ·   Read quorum 3/6
```
- **"The log is the database."** The writer sends only redo log records to storage. Storage nodes apply them to pages in the background. Traditional MySQL writes data pages + double-write buffer + binlog + redo to EBS, which costs far more network I/O.
- **Why 6 copies / 4-of-6?** Aurora wants to survive **AZ + 1** failure (a whole AZ down *plus* one more node) without losing data, and to survive a whole AZ loss while still writing. Losing an AZ (2 copies) leaves 4 → still a write quorum. Losing AZ+1 (3 copies) leaves 3 → still a read quorum, so you can rebuild without data loss.
- **10 GB segments** keep repair time short (seconds over a 10 Gbps link). Shorter repair windows make double faults less likely. This is the key argument of the Aurora SIGMOD 2017 paper.
- **Failover**: promotes a reader (they share storage, so there's no data copy). Typically **< 30s**, often ~10s with the AWS JDBC/Python wrappers that bypass DNS caching.
  - *"30s in test, 2 min in prod"* → client-side **DNS caching** (JVM caches DNS forever with a security manager, or for 30s by default), connection pools holding dead connections, no TCP keepalive / socket timeout. Fixes: AWS Advanced JDBC Wrapper, low DNS TTL in the JVM, RDS Proxy, sensible socket timeouts.
- **Aurora Global Database**: storage-level async replication to up to 10 secondary regions, typically **< 1s lag**. **Managed switchover** (planned, RPO 0) vs **failover** (unplanned, RPO = lag). Use with ARC or global writer endpoint.
- **Aurora Serverless v2**: scales in 0.5-ACU steps in seconds; can scale to 0 (auto-pause).
- **Aurora I/O-Optimized**: no per-I/O charges. Cheaper once I/O exceeds ~25% of the bill.
- **Cloning**: copy-on-write clones in minutes, good for test environments and blue/green.
- **Blue/Green Deployments**: managed staging copy with logical replication for major-version upgrades. Switchover typically under a minute.

---

## 4. DynamoDB: how it really works

### Partitioning
```
Table ──hash(partition key)──► partition 1 … partition N
   each partition: ~10 GB, ~3,000 RCU (strongly consistent reads) / 1,000 WCU max
   replicated to 3 AZs (Multi-Paxos leader per partition; writes ack after 2 of 3)
```
- Request router → metadata → storage node (leader for writes and strongly consistent reads; any replica for eventually consistent reads).
- **Adaptive capacity**: shifts throughput to hot partitions and **splits** them on sustained heat (split-for-heat). It can't split a *single key* that is hot.
- **On-demand** vs **provisioned (+ auto scaling)**: on-demand scales instantly up to double the previous peak; beyond that it ramps. Provisioned is cheaper at steady load. You can now set max throughput on on-demand tables (cost guard) and **warm throughput** (pre-warm before a launch).

### Hot partition / hot key fixes

| Cause | Fix |
|---|---|
| Low-cardinality PK (e.g. `status=ACTIVE`) | Redesign the key; write sharding (`ACTIVE#0..9`) |
| One celebrity item read-heavy | **DAX** or app cache; replicate the item N times with suffix |
| Time-series by date PK | Append a random/hashed suffix; scatter-gather reads |
| GSI hot | GSI throttling **back-pressures base table writes!** Watch GSI capacity |

### Data modelling (single-table design)
Model from **access patterns first**:
```
PK              SK                       Attributes
CUST#123        PROFILE                  name, email
CUST#123        ORDER#2026-11-01#9876    status, total
ORDER#9876      ITEM#1                   sku, qty
GSI1PK=STATUS#SHIPPED  GSI1SK=2026-11-01  (sparse index: only shipped orders)
```
- `Query` by PK + SK range (`begins_with`), never `Scan` in hot paths.
- 400 KB item limit. Put large blobs in S3 and store the pointer.
- **Transactions** (`TransactWriteItems`, up to 100 items) cost 2× capacity. Use conditional writes for optimistic locking (`version = :v`).
- **Streams** (24h retention, ordered per item) → Lambda for CDC, outbox pattern, aggregations.
- **TTL** deletes are free but **eventual** (typically within days). Filter expired items in reads.

### Global tables
- Multi-active (MREC): async replication, **last-writer-wins** conflict resolution, typically < 1s lag. Writes to the same item in two regions → one silently wins. Design **region-affinity** for writes (a user is homed in one region) to avoid conflicts.
- **Multi-region strong consistency (MRSC)**: RPO 0, strongly consistent reads in any region, at higher write latency. Needs 3 regions (or 2 + a witness).

### DynamoDB and the Oct 2025 us-east-1 event
The **regional endpoint's DNS record** (`dynamodb.us-east-1.amazonaws.com`) was emptied by a race condition in DynamoDB's internal DNS automation. The data was fine, but nobody (including internal AWS services like EC2's control plane) could resolve the endpoint. Lesson: even "serverless, multi-AZ" services have **regional** failure modes. Multi-region designs (global tables) are how you survive regional events. Full analysis: [06-aws-outage-postmortems.md](../03-architecture-deep-dives/06-aws-outage-postmortems.md).

---

## 5. Caching

| Pattern | How | Risk |
|---|---|---|
| **Cache-aside** (lazy) | Read cache → miss → DB → populate | Stampede on cold cache; stale data |
| Write-through | Write DB + cache together | Write latency; caching unread data |
| Write-behind | Write cache, flush async | Data loss |
| Refresh-ahead | Refresh before TTL expires | Complexity |

### Cache stampede (thundering herd) prevention
1. **Request coalescing / single-flight**: one loader per key; others wait.
2. **Jittered TTLs** so keys don't expire together.
3. **Stale-while-revalidate**: serve stale while one worker refreshes.
4. **Probabilistic early expiration** (XFetch).
5. **Warm the replacement** before failover (Netflix EVCache warms new clusters from replicas before shifting traffic).
6. Protect the DB: concurrency limits / load shedding on the DB tier, so a cold cache causes slowness, not collapse.

ElastiCache cluster mode: 16,384 hash slots across shards; Multi-AZ with automatic failover (replica promoted). **ElastiCache Serverless** removes capacity planning. **MemoryDB** adds a durable multi-AZ transaction log (use it as a primary database).

---

## 6. Senior interview questions — model answers

<details><summary><b>Q1. DynamoDB vs Aurora for a new order service at 50k writes/sec peak?</b></summary>

At 50k WPS, DynamoDB scales horizontally with no connection management, and the access patterns (get order, list orders by customer, by status) map well to PK/SK + a GSI. Aurora's single writer would need a very big instance and careful tuning; sharding Aurora brings operational pain. Choose DynamoDB, use Streams → analytics store (Redshift/S3 via zero-ETL) for ad-hoc queries, and transactions for order + inventory. If strong cross-entity SQL semantics are core (complex reporting in the same store), consider Aurora DSQL or a CQRS split.

</details>

<details><summary><b>Q2. Aurora Global DB: walk me through unplanned region failover.</b></summary>

1) Detect (canaries + replication lag + ARC readiness). 2) Decide (humans, or automation with guardrails). 3) **Failover** (`failover-global-cluster` with `--allow-data-loss`), which promotes the secondary. RPO = replication lag at failure (usually < 1s, report it). 4) Shift app traffic (ARC routing control / Route 53). The app reconnects via the global writer endpoint. 5) After recovery, the old primary rejoins as secondary (it had divergent writes; Aurora takes a snapshot of the old primary for recovering lost writes). Practise it quarterly.

</details>

<details><summary><b>Q3. Why is "eventually consistent read" cheaper in DynamoDB?</b></summary>

It can be served by any of the 3 replicas (not just the leader) and costs half an RCU. It may miss writes from the last ~second. Strongly consistent reads go to the leader, cost 1 RCU per 4 KB, and aren't supported on GSIs.

</details>

<details><summary><b>Q4. How do you do a zero-downtime major version upgrade of PostgreSQL on RDS?</b></summary>

RDS Blue/Green Deployments: create green (new major version, logical replication from blue), test against green, then switchover (guardrails check replication lag, blocks writes briefly, renames endpoints). Downtime is typically under a minute. Pre-check: extension compatibility, `pg_upgrade` checks, DDL during replication (logical replication doesn't carry DDL), sequences, and large objects.

</details>

---

## Further reading
- Paper: *Amazon Aurora: Design Considerations for High Throughput Cloud-Native Relational Databases* (SIGMOD 2017).
- Paper: *Amazon DynamoDB: A Scalable, Predictably Performant, and Fully Managed NoSQL Database Service* (USENIX ATC 2022).
- Book: Alex DeBrie, *The DynamoDB Book* (single-table design).
- Netflix: EVCache, KV Data Abstraction, see [03-netflix-data-platform.md](../03-architecture-deep-dives/03-netflix-data-platform.md).
