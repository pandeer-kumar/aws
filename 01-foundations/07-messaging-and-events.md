# 07 · Messaging & Event-Driven Architecture: SQS, SNS, EventBridge, Kinesis, MSK, Step Functions

> **Interviewer lens.**
> - "SQS standard vs FIFO: what does 'at-least-once' force you to build?"
> - "Explain visibility timeout and what happens when processing takes longer than it."
> - "SNS+SQS fan-out vs EventBridge vs Kinesis: choose for three different scenarios."
> - "A queue backlog of 50M messages after an outage. How do you drain it without killing the DB?"
> - "Design an idempotent payment consumer."
> - "When would you use Step Functions instead of choreography with events?"

---

## 1. The decision table

| | SQS | SNS | EventBridge | Kinesis Data Streams | MSK (Kafka) |
|---|---|---|---|---|---|
| Model | Queue (pull) | Pub/sub push | Event bus with rules (content filtering) | Ordered log, shards | Ordered log, partitions |
| Consumers | Competing consumers | Many subscribers | Up to 5 targets per rule, many rules | Multiple independent readers | Consumer groups |
| Ordering | FIFO queues (per group ID) | FIFO topics | None | Per shard (partition key) | Per partition |
| Replay | ❌ (once deleted it's gone) | ❌ | Archive & replay | ✅ 24h → 365d | ✅ retention-based |
| Throughput | ~unlimited (standard); FIFO high-throughput mode up to tens of thousands TPS | very high | soft limits per region (raisable) | 1 MB/s or 1k rec/s in per shard | broker-sized |
| Latency | ms | ms | ~100s of ms typical | ~70ms (enhanced fan-out) – 200ms+ | ms |
| Best for | Work queues, buffering, decoupling | Fan-out, notifications | SaaS/AWS events, cross-account routing, loose coupling | Streaming analytics, CDC, ordered event logs | Kafka ecosystem, very high throughput |

---

## 2. SQS mechanics

```
Producer ─SendMessage─► Queue ─ReceiveMessage (long poll ≤20s)─► Consumer
                                 │ message becomes INVISIBLE for VisibilityTimeout
                                 │ consumer must DeleteMessage before timeout expires,
                                 │ otherwise the message reappears → redelivery
                                 └─ after maxReceiveCount → Dead-Letter Queue (DLQ)
```
- **At-least-once** delivery (standard queues) and best-effort ordering. Duplicates are rare but *will* happen. **You must be idempotent.**
- **FIFO**: exactly-once *processing* within a 5-minute dedup window (by `MessageDeduplicationId`), strict order per `MessageGroupId`. Throughput scales with the number of distinct group IDs.
- **Visibility timeout** should be at least 6× the Lambda function timeout when Lambda is the consumer (AWS guidance). For long jobs, call `ChangeMessageVisibility` as a heartbeat.
- **Long polling** (`WaitTimeSeconds=20`) cuts empty receives and cost.
- Max message size is 1 MiB (it was 256 KB until 2025). For bigger payloads, use the S3 extended-client pattern: store the payload in S3 and send a pointer. Retention is up to 14 days.
- **DLQ redrive**: move messages back to the source after a fix (console/API `StartMessageMoveTask`).
- **Fair queues** (newer): mitigate noisy-neighbour tenants in standard queues using a message group ID, so one tenant's backlog doesn't delay others' dwell time.

### Idempotency recipe (interview favourite)
```
1. Every message carries an idempotency key (orderId + operation, or producer-generated UUID).
2. Consumer: conditional write to an idempotency table
   PutItem(pk=key, status=IN_PROGRESS, ttl=+24h) with attribute_not_exists(pk)
   ├─ fails → already processed/processing → return stored result / skip
   └─ succeeds → do the side effect → update status=DONE + result
3. Side effects downstream should ALSO be idempotent (pass the key to the payment provider).
```
Powertools for AWS Lambda ships an `idempotency` utility that implements exactly this on DynamoDB.

---

## 3. SNS

- Push to SQS, Lambda, HTTP(S), email, SMS, Firehose.
- **Subscription filter policies** (on message attributes or body) avoid every consumer receiving everything.
- **SNS → SQS fan-out** is the canonical pattern. Each consumer gets its own queue (buffering, retries and a DLQ per consumer).
- Raw message delivery avoids the JSON envelope.
- FIFO topics → FIFO queues for ordered fan-out.

## 4. EventBridge

- **Event buses** (default bus for AWS service events, custom buses, partner buses for SaaS).
- **Rules** match on JSON content (prefix, numeric ranges, `anything-but`, exists, wildcard).
- **Cross-account/cross-region** delivery: bus-to-bus. Typical pattern: a central "event hub" account.
- **Archive & replay**, **Schema Registry** (discovery + code bindings).
- **Pipes**: point-to-point source → filter → enrich → target (e.g. DynamoDB Stream → filter → Lambda enrich → Step Functions).
- **Scheduler**: millions of one-off or cron schedules (replaces ad-hoc cron instances).
- Delivery is at-least-once with retries for up to 24h, then the target's DLQ. **Not ordered.**

## 5. Kinesis Data Streams

- Shard = 1 MB/s or 1,000 records/s in; 2 MB/s out (shared) or 2 MB/s **per consumer** with enhanced fan-out.
- **Partition key** → shard (MD5 hash). Hot partition key = hot shard → `ProvisionedThroughputExceeded`.
- On-demand mode autoscales (up to 2× previous peak quickly).
- Consumers: KCL (leases in DynamoDB, checkpointing), Lambda ESM, Managed Flink, Firehose.
- **Ordering + retries**: a failing record blocks its shard for the consumer. Use bisect + max retries + an on-failure destination.

## 6. Step Functions

| | Standard | Express |
|---|---|---|
| Duration | up to 1 year | 5 minutes |
| Semantics | Exactly-once workflow execution | At-least-once (async) / at-most-once (sync) |
| Pricing | Per state transition | Per request + duration |
| Use | Long business processes, human approval (`.waitForTaskToken`), sagas | High-volume event processing, IoT ingestion |

- **Saga pattern** for distributed transactions: each step has a compensating action in `Catch`.
- Distributed Map: process millions of S3 objects in parallel.
- **Orchestration (Step Functions) vs choreography (events):** orchestration gives visibility, central error handling and clear ownership of the flow, at the cost of coupling to the orchestrator. Choreography is loosely coupled, but the flow is spread across services and hard to trace. Senior answer: orchestrate *within* a bounded context, choreograph *between* contexts.

---

## 7. Draining a massive backlog safely (a real ops scenario)

After a downstream outage, 50M messages sit in SQS. If you scale consumers to max, they'll stampede the recovering DB and cause outage #2.

1. **Cap concurrency** (Lambda ESM `MaximumConcurrency`, or reserved concurrency) at what the DB can handle. Raise it gradually while watching DB metrics.
2. **Prioritize:** move new traffic to a fresh queue, or process LIFO for freshness if old messages are less valuable. The Builders' Library *Avoiding insurmountable queue backlogs* discusses dropping stale work and per-tenant queues.
3. **Drop/park expired work**: messages older than their usefulness (e.g. notifications from 6h ago) go to a parking queue.
4. **Isolate tenants**: one noisy tenant shouldn't block everyone (fair queues, shuffle-sharded queues).
5. Watch `ApproximateAgeOfOldestMessage`, not just depth. That's your customer-facing latency.

---

## 8. Senior interview questions — model answers

<details><summary><b>Q1. Exactly-once delivery: possible?</b></summary>
Not end to end over an unreliable network. You get **at-least-once delivery + idempotent processing = effectively-once results**. SQS FIFO deduplicates within 5 minutes; Kafka has idempotent producers and transactions (exactly-once within Kafka). Any external side effect (charging a card, sending an email) still needs an idempotency key at the boundary.
</details>

<details><summary><b>Q2. Transactional outbox: why and how on AWS?</b></summary>
Problem: writing to the DB and publishing an event are two systems, so a crash between them loses or duplicates the event. Outbox: write the business row *and* an outbox row in one DB transaction. A relay publishes the outbox rows. On AWS: DynamoDB `TransactWriteItems` (order + outbox item) with **DynamoDB Streams → Lambda/EventBridge Pipes** as the relay, or Aurora + Debezium/MSK Connect CDC. Consumers stay idempotent because the relay is at-least-once.
</details>

<details><summary><b>Q3. Kinesis vs MSK?</b></summary>
Kinesis: serverless-ish, AWS-native IAM, shard model, simpler ops, 365-day retention max, fixed per-shard limits. MSK (or MSK Serverless / Express brokers): Kafka API compatibility, ecosystem (Connect, Streams, Schema Registry), finer tuning, higher per-partition throughput. You manage partitions/brokers (less so with Serverless/Express) and **cross-AZ replication traffic cost** (use rack-aware consumers). Choose by ecosystem lock-in and team skills. Netflix's Keystone runs on Kafka (it predates MSK).
</details>

---

## Further reading
- Builders' Library: *Avoiding insurmountable queue backlogs*; *Making retries safe with idempotent APIs*.
- Netflix Tech Blog: *Keystone Real-time Stream Processing Platform* (2018). See [03-netflix-data-platform.md](../03-architecture-deep-dives/03-netflix-data-platform.md).
- Gregor Hohpe, *Enterprise Integration Patterns*.
