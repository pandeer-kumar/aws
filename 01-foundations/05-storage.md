# 05 · Storage: S3, EBS, EFS, FSx: How They Work and How They Fail

> **Interviewer lens.**
> - "S3 has been strongly consistent since Dec 2020. What exactly does that guarantee, and what *still* isn't atomic?"
> - "How does S3 scale request rate per prefix? Do you still need random prefixes?"
> - "gp3 vs io2 Block Express: when would you pay for io2? What is the EBS bandwidth bottleneck on a given instance?"
> - "Design a 5 PB data lake with lifecycle, encryption, cross-region DR and cost controls."
> - "Someone ran `aws s3 rm --recursive` on prod. What would have saved you?"

---

## 1. S3: the object store

### Data model and guarantees
- Bucket (global namespace, regional data) → key → object (up to **50 TB**, multipart required above 5 GB; recommended above ~100 MB).
- **Durability 11 nines.** Objects are stored across ≥3 AZs (except One Zone-IA / Express One Zone).
- **Strong read-after-write consistency** for PUT/DELETE and LIST, at no cost. After a successful PUT, any subsequent GET/LIST sees it.
  - **Not** guaranteed: atomic multi-object operations, rename (there is no rename; "move" is copy + delete), or ordering between *concurrent* writers to the same key (last writer wins by timestamp).
  - **Conditional writes** (`If-None-Match: *` for create-if-absent, `If-Match: <etag>` for compare-and-swap) are now supported. You can build locks/leases and safe concurrent writers (used by table formats like Iceberg/Delta).

### Performance model
- Per **prefix**: ≥ **3,500 PUT/COPY/POST/DELETE** and **5,500 GET/HEAD** per second. S3 auto-partitions busy prefixes. Throughput ramps up as S3 splits partitions, so **sudden** jumps cause temporary `503 Slow Down`.
- You no longer need random hash prefixes, but you *do* need enough distinct prefixes for very high request rates, plus retries with backoff on 503.
- Large objects: parallel **multipart upload** and **byte-range GETs** (8–16 MB parts) to saturate NIC bandwidth.
- **S3 Express One Zone:** single-AZ directory buckets, single-digit-ms latency, up to hundreds of thousands of TPS per bucket. For ML training/analytics scratch.

### Storage classes and lifecycle

| Class | Min duration | Retrieval | Use |
|---|---|---|---|
| Standard | — | ms | Hot |
| **Intelligent-Tiering** | — | ms (archive tiers optional) | Unknown/changing access. Small per-object monitoring fee; objects <128 KB are never moved |
| Standard-IA / One Zone-IA | 30 d | ms, per-GB retrieval fee | Infrequent |
| Glacier Instant Retrieval | 90 d | ms | Quarterly-access archives |
| Glacier Flexible Retrieval | 90 d | min–hours | Backups |
| Glacier Deep Archive | 180 d | 12–48 h | Compliance |

**Cost traps:** lifecycle transitions are charged **per object**, so millions of small objects can cost more to transition than to keep. Min-duration charges apply. Incomplete multipart uploads accumulate forever unless a lifecycle rule aborts them.

### Protection: what would have saved you from `rm --recursive`
1. **Versioning.** A delete adds a *delete marker*; previous versions remain.
2. **MFA Delete** (root only, awkward) or better: **Object Lock** (WORM; governance or compliance mode) for backups.
3. **AWS Backup for S3** / **replication** to a separate account with a different owner (protects against account compromise).
4. Deny `s3:DeleteObject*` / `s3:PutLifecycleConfiguration` in a bucket policy, except for a break-glass role.
5. Alerts on CloudTrail data events for mass deletes.

### Encryption
- All new objects are encrypted (SSE-S3 default since Jan 2023).
- **SSE-KMS:** every GET/PUT calls KMS. At high request rates you'll hit KMS limits (5.5k–100k req/s per account depending on region). **S3 Bucket Keys** cut KMS calls by ~99% by deriving a bucket-level key.
- **DSSE-KMS** = dual-layer for compliance. **SSE-C** = you supply keys (AWS is moving away from it; it can be blocked).

### Replication
- **CRR/SRR**: asynchronous, new objects only (use **Batch Replication** for existing ones). **Replication Time Control (RTC)**: 99.99% of objects within 15 min, with metrics. Can replicate to another account and change the owner.
- **Multi-Region Access Points**: one global endpoint, routes to the nearest bucket, with failover controls (active/passive). Uses SigV4a.

### Security defaults to know
- **Block Public Access** is on by default for new buckets (account- and bucket-level).
- **Object Ownership = Bucket owner enforced** (ACLs disabled) is the default.
- Use **Access Points** for per-application policies on shared data lakes. Use **S3 Access Grants** to map IdP identities to S3 prefixes.

---

## 2. EBS: network block storage

```
Instance ──NVMe──► Nitro card ──network──► EBS storage servers (replicated within ONE AZ)
```

| Volume | IOPS | Throughput | Notes |
|---|---|---|---|
| **gp3** | 3,000 baseline, up to 16,000 (newer limits higher) | 125 → 1,000 MB/s | Provision IOPS & throughput independently. Default choice |
| gp2 | 3 IOPS/GB burst bucket | | Legacy; migrate to gp3 (~20% cheaper) |
| **io2 Block Express** | up to 256,000 | 4,000 MB/s | Sub-ms latency, 99.999% durability; big Oracle/SQL Server |
| st1 / sc1 | Throughput-oriented HDD | | Big sequential (logs, Kafka cold) |

**The bottleneck is often the instance, not the volume.** Every instance type has a max EBS bandwidth/IOPS (e.g. a small `m7i.large` bursts to ~10 Gbps EBS but has a lower baseline). Check `EBSIOBalance%` and `EBSByteBalance%`, and volume `VolumeQueueLength`.

Other facts:
- Volumes are **AZ-bound**. Snapshots are regional (stored in S3, incremental); copy them cross-region for DR.
- **Restored from snapshot = lazy-loaded.** First reads of each block are slow. Use **Fast Snapshot Restore** (per AZ, costly) or pre-warm with `fio`/`dd` before production traffic. *A classic DR-drill surprise.*
- **Multi-Attach** (io1/io2) needs a cluster-aware filesystem. It is not a shortcut to shared storage.
- Snapshots: **Recycle Bin** retention rules protect against accidental deletes; **EBS Snapshot Lock** for ransomware protection.

---

## 3. File storage

| | EFS | FSx for Lustre | FSx for NetApp ONTAP | FSx for Windows | FSx for OpenZFS |
|---|---|---|---|---|---|
| Protocol | NFSv4.1 | Lustre | NFS, SMB, iSCSI | SMB | NFS |
| For | Shared Linux files, containers | HPC/ML scratch, S3-linked | Lift-and-shift NetApp, multiprotocol | Windows/AD | Low-latency NFS |
| Scale | Elastic | 100s GB/s | | | |

EFS gotchas: **Bursting** throughput mode is tied to size, so small filesystems throttle. Use **Elastic** throughput. Per-operation latency is higher than EBS, so it's poor for many small files (node_modules, git checkouts).

---

## 4. Senior interview questions — model answers

<details><summary><b>Q1. Design a 5 PB data lake on S3.</b></summary>

- **Layout:** raw / curated / consumption zones in separate buckets (or accounts). Partition by date and high-cardinality-safe keys. Use **Apache Iceberg** tables (S3 Tables or self-managed via Glue Catalog) for ACID, schema evolution and compaction.
- **Access:** Lake Formation for table/column/row permissions; Access Points per consumer; IAM Identity Center + Access Grants.
- **Cost:** Intelligent-Tiering for curated data; lifecycle to Glacier IR for raw after 90 days; abort incomplete MPUs; S3 Storage Lens for anomaly detection; compaction to avoid small files (they hurt both cost and Athena/Spark performance).
- **Security:** SSE-KMS with Bucket Keys, data perimeter policies, Macie on raw.
- **DR:** CRR with RTC for critical curated tables to another region/account. Raw data can often be rebuilt from source, so make a conscious RPO decision per zone.
- **Ops:** S3 Inventory + Athena for audits; CloudTrail data events on sensitive prefixes only (cost).

</details>

<details><summary><b>Q2. A batch job gets 503 Slow Down writing to S3.</b></summary>

It's exceeding per-prefix request rates before S3 has re-partitioned. Fixes: spread writes across more prefixes (e.g. `/date/hour/shard-00..63/`), use exponential backoff with jitter (SDKs do this, so check the retry config), batch small objects into bigger files, and ramp up gradually. If it's KMS throttling (`ThrottlingException` from KMS), enable Bucket Keys.

</details>

<details><summary><b>Q3. Your DR test restored the DB from EBS snapshots, but the app was 10x slower for an hour. Why?</b></summary>

Lazy loading from S3: blocks are fetched on first access. Fix with Fast Snapshot Restore on the DR AZ's critical volumes, a pre-warm script, or use a replicated database (Aurora Global, read replica) instead of snapshots for low-RTO tiers. This is why RTO must be measured in drills, not estimated.

</details>

<details><summary><b>Q4. How would you make an S3 bucket the source of truth for an append-only event log with multiple writers?</b></summary>

Use conditional writes. Each writer creates `log/<seq>.json` with `If-None-Match: *`, and a writer that loses the race gets a `412 Precondition Failed` and retries with the next sequence. A manifest pointer updated with `If-Match: <etag>` gives compare-and-swap. This is the core idea behind table formats' commit protocols. Mention the trade-offs: one round trip per commit, and contention at high write concurrency (use partitioned logs).

</details>

---

## Further reading
- AWS docs: *Best practices design patterns: optimizing Amazon S3 performance*.
- Werner Vogels blog *All Things Distributed*: "Diving deep on S3 consistency" (how S3 implemented strong consistency).
- USENIX FAST '23 keynote: *Building and operating a pretty big storage system called S3* (Andy Warfield).
- 2017 S3 outage (us-east-1) post-event summary: a mistyped command removed too much capacity. See [06-aws-outage-postmortems.md](../03-architecture-deep-dives/06-aws-outage-postmortems.md).
