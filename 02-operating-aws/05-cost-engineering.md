# 05 · Cost Engineering (FinOps) for Engineers

> **Interviewer lens.**
> - "Our AWS bill grew 40% last quarter with flat traffic. How do you find out why?"
> - "Savings Plans vs RIs vs Spot: build me a purchasing strategy for a $10M/year EC2 spend."
> - "Name five AWS costs that surprise teams."
> - "How do you give 50 teams cost accountability?"

---

## 1. Visibility first
- **Cost and Usage Report (CUR 2.0 / Data Exports)** → S3 → Athena/QuickSight. This is the only source with line-item, resource-level detail.
- **Cost Allocation Tags** (activate them!) + **Cost Categories** (map accounts/tags → teams/products).
- **Cost Explorer**, **Cost Anomaly Detection** (ML alerts per service/account/tag), **Budgets** with actions (e.g. apply a deny SCP to a sandbox when the budget is exceeded).
- **Compute Optimizer** (right-sizing EC2/EBS/Lambda/ECS/RDS), **Trusted Advisor**, **Cost Optimization Hub** (consolidated recommendations).

### "Bill +40%, traffic flat": the investigation
1. Cost Explorer grouped by **service → usage type → account → tag**, month over month. Find the top-3 movers.
2. Typical culprits: **data transfer** (`DataTransfer-Regional-Bytes` = cross-AZ; `NatGateway-Bytes`), **CloudWatch Logs ingestion**, **EBS snapshots** never cleaned, **gp2 volumes**, idle RDS/ELB/NAT in dev accounts, unattached EIPs / public IPv4, Savings Plans **expired**, a new feature with chatty DynamoDB scans, S3 request costs from small files, KMS calls without Bucket Keys.
3. CUR + Athena down to `line_item_resource_id`.
4. Fix, then add a guardrail (anomaly alert, Config rule, SCP, lifecycle policy).

---

## 2. Purchasing strategy for $10M/yr compute
1. **Right-size and modernize first** (Graviton, gp3, Spot for stateless/batch). Don't commit to waste.
2. Cover the **steady baseline** (the lowest hourly usage over the past 30–90 days, after right-sizing) with **Compute Savings Plans** (flexible across EC2/Fargate/Lambda, families, regions). Laddered: buy in tranches every quarter so commitments expire in a staggered way. Target 70–85% coverage of the baseline.
3. **EC2 Instance Savings Plans** for very stable families (deeper discount, less flexibility).
4. **Spot** for fault-tolerant capacity above baseline (CI, batch, stateless web behind ALB with mixed instances).
5. **On-Demand** for the remaining peaks.
6. **ODCRs** only where capacity *assurance* matters (DR, GPU). Combine them with SPs for the discount.
7. Monitor SP **utilisation** (are you using what you committed?) and **coverage** (how much is still On-Demand?).

---

## 3. Architectural cost levers

| Lever | Typical saving |
|---|---|
| Graviton | 20–40% price/perf |
| gp2 → gp3 | ~20% |
| S3 Intelligent-Tiering / lifecycle | 40–70% on cold data |
| VPC gateway endpoints for S3/DynamoDB | Removes NAT processing |
| AZ-affine traffic (topology-aware routing in K8s, Kafka rack awareness) | Cuts cross-AZ $0.01/GB each way |
| CloudFront in front of S3/ALB | Lower egress than direct + caching |
| Lambda right-size memory (Power Tuning) | 10–50% |
| Aurora I/O-Optimized when I/O > ~25% of spend | Variable |
| Turn off non-prod nights/weekends (Instance Scheduler) | ~65% of non-prod compute |
| Delete: unattached EBS, old snapshots, idle LBs, unused EIPs, orphaned ENIs | Pure savings |

---

## 4. Netflix on cost
Netflix has written about **cloud efficiency** as an engineering discipline: they give teams cost visibility ("Netflix Cloud Efficiency" dashboards attributing spend to services) instead of central approval gates. That fits their "context, not control" culture. Lesson for interviews: **show the cost to the owners and make the efficient path the easy path** (paved road defaults: right-sized instance templates, autoscaling on by default).

---

## 5. Senior interview questions — model answers

<details><summary><b>Q1. How do you give 50 teams cost accountability?</b></summary>

Account-per-team-per-env (cleanest attribution) + mandatory tags enforced by Tag Policies/SCP + Cost Categories for shared costs (split network/shared services by usage or a fixed key). Monthly showback (chargeback if finance wants it) with unit-cost metrics: cost per 1k requests, per customer, per stream hour. Anomaly alerts routed to team channels. Budgets per account. Make efficiency a quarterly engineering KPI alongside reliability.

</details>

<details><summary><b>Q2. Five AWS costs that surprise teams?</b></summary>

1) NAT Gateway data processing. 2) Cross-AZ data transfer (replication, chatty microservices, cross-zone LB). 3) CloudWatch Logs ingestion + infinite retention. 4) Public IPv4 addresses ($0.005/hr each since 2024). 5) S3 request costs and lifecycle transition costs on billions of small objects. Bonus: KMS request costs, Config recorder in high-churn accounts, EBS snapshots of deleted volumes, data transfer to the internet without CloudFront, idle provisioned DynamoDB/Kinesis.

</details>

<!-- nav -->
---

← [04 · Resilience Engineering & Disaster Recovery](04-resilience-and-dr.md) · [🏠 Home](../README.md) · [Deep Dive 01 · Netflix: From One Data Center to Four-Region Active-Active on AWS](../03-architecture-deep-dives/01-netflix-multi-region-active-active.md) →
