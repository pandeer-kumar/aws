# 01 · Observability: Knowing What's Broken Before Customers Do

> **Interviewer lens.**
> - "What do you alarm on? Show me how you'd turn an SLO into CloudWatch alarms."
> - "CloudWatch costs exploded. Why, and how do you fix it without going blind?"
> - "Walk me through debugging a p99 latency regression across 12 microservices."
> - "CloudTrail vs Config vs VPC Flow Logs vs X-Ray: which answers which question?"
> - "How do you avoid alarm fatigue in a 50-team org?"

---

## 1. The signals and the AWS service for each

| Signal | Question it answers | AWS native | Common alternatives |
|---|---|---|---|
| **Metrics** | Is it healthy? How much? | CloudWatch Metrics, Managed Prometheus (AMP) | Datadog, Atlas (Netflix) |
| **Logs** | What happened in detail? | CloudWatch Logs (+ Logs Insights), OpenSearch | Splunk, Loki |
| **Traces** | Where did the time go across services? | X-Ray / CloudWatch Application Signals (OpenTelemetry) | Jaeger, Honeycomb |
| **Audit** | Who changed what? | **CloudTrail** (management + data events), CloudTrail Lake | |
| **Config state** | What does it look like now and how did it change? | **AWS Config** (recorder + rules + conformance packs) | |
| **Network** | Who talked to whom? | VPC Flow Logs, Network Firewall logs, Reachability Analyzer | |
| **Synthetic / RUM** | What do users see? | CloudWatch Synthetics (canaries), RUM, Internet Monitor | |

---

## 2. CloudWatch internals worth knowing

- **Metric identity** = namespace + name + **dimensions**. Every unique dimension combination is a separate metric ($0.30/metric/month at first tier). Putting `userId` or `requestId` in a dimension = **cardinality explosion** = surprise bill.
- **Resolution:** standard 1-minute; high-resolution 1-second (alarms down to 10s).
- **Embedded Metric Format (EMF):** write a structured JSON log line and CloudWatch extracts metrics asynchronously. No `PutMetricData` API calls in the hot path (cheaper, no throttling).
- **Metric math & anomaly detection** bands; **metrics insights** SQL queries across many metrics.
- **Composite alarms:** AND/OR across alarms. Use them to page only when *customer impact* AND *cause* are both true, and to suppress child alarms during maintenance.
- **Logs:** ingestion ~$0.50/GB is the main cost. The **Infrequent Access** log class halves it for logs you rarely query. Set retention on *every* log group (default is never expire!).
- **Contributor Insights:** top-N talkers (which IP / API key / partition key is hot).

### Cost control without losing visibility
1. Retention policies everywhere (e.g. 30d hot; export to S3 for long-term).
2. Sample debug logs; log at INFO in prod; structured logs only.
3. Move high-volume logs (ALB, VPC Flow) to **S3 + Athena** instead of CloudWatch Logs.
4. Reduce metric dimensions; use EMF.
5. Turn off detailed metrics you don't alarm on or view.

---

## 3. From SLO to alarms (the senior way)

```
SLI:   proportion of successful (non-5xx, < 300ms) requests at the ALB
SLO:   99.9% over 30 days  →  error budget = 0.1% = 43.2 minutes of "full outage" equivalent
Alarm on BURN RATE, not on raw error rate:
  Fast burn: 14.4× budget rate over 1h AND 5m windows  → page (2% of budget burnt in 1h)
  Slow burn:  6× over 6h AND 30m                        → page
              1× over 3d                                → ticket
```
In CloudWatch: metric math computes `errors/requests` per window. Two alarms per window pair → **composite alarm** (`ALARM(long) AND ALARM(short)`). The short window lets the alarm reset fast after recovery. CloudWatch Application Signals now has SLO objects with burn-rate alarms built in.

**What to page on:** symptoms customers feel (availability, latency SLO burn), plus a few "definitely about to hurt" causes (disk 95%, certificate expiring in 7 days, replication lag above RPO). **Everything else is a ticket or a dashboard.**

---

## 4. Debugging a p99 regression across microservices

1. **Scope:** when did it start? Which API? All AZs or one? All customers or one tenant? (Per-AZ and per-tenant dimensions pay off here.)
2. **Correlate with change:** deployments (pipeline events), config pushes (AppConfig), infra changes (CloudTrail/Config timeline), traffic mix change.
3. **Traces:** X-Ray / Application Signals service map. Find the segment whose latency grew. Compare trace *exemplars* from slow vs fast requests.
4. **Resource saturation (USE method: Utilisation, Saturation, Errors)** on the culprit: CPU throttling, GC, connection pool wait, DB `DBLoad` / Performance Insights top SQL, DynamoDB throttles, Lambda init durations.
5. **Dependencies:** downstream latency and retry amplification (retries at 3 layers → 27× load).
6. Mitigate first (roll back, shift traffic), root-cause second.

Brendan Gregg's (ex-Netflix) *Linux Performance Analysis in 60,000 Milliseconds* is the checklist for step 4 on a single host: `uptime`, `dmesg | tail`, `vmstat 1`, `mpstat -P ALL 1`, `pidstat 1`, `iostat -xz 1`, `free -m`, `sar -n DEV 1`, `sar -n TCP,ETCP 1`, `top`.

---

## 5. Audit and compliance plumbing

- **CloudTrail organization trail** → central log-archive account S3 bucket (Object Lock, KMS, restricted access) + CloudTrail Lake or Athena for queries.
- Management events are free (first copy); **data events** (S3 object-level, Lambda invoke, DynamoDB item) cost per event. Enable them selectively with advanced event selectors.
- **Config** recorder in every account/region (exclude high-churn types or use periodic recording to control cost) → aggregator in the audit account → conformance packs (CIS, PCI) → auto-remediation via SSM Automation.
- **Security Hub** aggregates GuardDuty, Inspector, Macie, Config findings. See [03-security-and-governance.md](./03-security-and-governance.md).

---

## 6. Netflix reference: observability at scale
- **Atlas**: Netflix's in-memory dimensional time-series system, built because existing tools couldn't handle their metric volume. Lesson: they keep recent data in memory for fast operational queries and push long-term data elsewhere.
- **Edgar / distributed tracing** and **Mantis** (real-time stream processing for operational insights) show the same idea: high-cardinality debugging data (per device, per request) is better processed as streams and sampled than stored as metrics.
- Outlier detection on server metrics to auto-terminate bad instances (*"Tracking down the Villains: Outlier Detection at Netflix"*).

---

## 7. Senior interview questions — model answers

<details><summary><b>Q1. How do you avoid alarm fatigue across 50 teams?</b></summary>

Page only on SLO burn and a short list of imminent-harm causes. Every page must be actionable and have a runbook link. Use composite alarms to suppress noise (dependency down → don't page all 20 dependents). Review alert quality monthly: pages per on-call shift, % actionable. Delete alarms nobody acted on. A platform team provides standard alarm modules in IaC so teams start with sensible defaults.

</details>

<details><summary><b>Q2. Prove who deleted a production security group last Tuesday.</b></summary>

CloudTrail: `eventName=DeleteSecurityGroup` with `requestParameters.groupId`, across the org trail (CloudTrail Lake SQL or Athena). Read `userIdentity` (assumed-role ARN + session name = the Identity Center user), `sourceIPAddress`, `userAgent`. AWS Config shows the resource timeline and its relationships before deletion. If the session name is generic (e.g. a CI role), trace back via `sts:AssumeRole` events to the source identity. Enforce `sts:SourceIdentity` for this reason.

</details>

<!-- nav -->
---

← [07 · Messaging & Event-Driven Architecture: SQS, SNS, EventBridge, Kinesis, MSK, Step Functions](../01-foundations/07-messaging-and-events.md) · [🏠 Home](../README.md) · [02 · Infrastructure as Code & Safe Deployments](02-iac-and-deployments.md) →
