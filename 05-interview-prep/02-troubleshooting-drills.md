# Troubleshooting Drills (Scenario Interviews)

Senior interviews often go: "Here's a symptom. Go." Practise the **method**: clarify → scope (who/where/when) → hypotheses ranked by likelihood → evidence (which metric/log) → mitigate first → root cause → prevent.

---

### Drill 1: "Users in one region see 5% 5xx since 14:02."
- **Scope:** all AZs or one? (ALB metrics by AZ.) All endpoints? All targets or a few? Did a deploy/config change happen at 14:00? (Pipeline, AppConfig, CloudTrail.)
- **Hypotheses:** bad deploy on a subset (canary leaked) → roll back; one AZ grey failure → zonal shift; downstream dependency throttling (DynamoDB `ThrottledRequests`, RDS connections) → shed/limit; certificate/secret rotation broke a subset of hosts.
- **Mitigate:** roll back / zonal shift / shed load. **Then** RCA.

### Drill 2: "Lambda duration doubled and costs are up, no code change."
Downstream latency increase (X-Ray), a cold-start increase from a dependency size change (layer update), throttling causing retries, a VPC NAT/DNS problem, or the memory setting changed by IaC drift. Check `Init Duration` in REPORT logs, X-Ray subsegments, and CloudTrail `UpdateFunctionConfiguration`.

### Drill 3: "EKS pods stuck in `ContainerCreating`."
`kubectl describe pod`: `failed to assign an IP address` → subnet IP exhaustion / ENI limits (prefix delegation, secondary CIDR). Image pull errors → ECR auth/endpoint or rate limits (pull-through cache). Volume attach → EBS CSI / AZ mismatch (the PV is in another AZ).

### Drill 4: "DynamoDB `ProvisionedThroughputExceededException` but consumed capacity is only 40% of provisioned."
A hot partition/key (CloudWatch Contributor Insights for DynamoDB → top keys), or GSI throttling back-pressuring the base table, or bursts within the minute (the metrics average over 1 minute). Fix the key design, write-shard, use on-demand, add a cache for hot reads.

### Drill 5: "S3 → Lambda → OpenSearch pipeline loses documents occasionally."
Async invoke retries exhausted (no DLQ/on-failure destination configured), Lambda throttled (reserved concurrency) with events expired by max age, OpenSearch 429 (bulk rejections) without retry, S3 event notification config overlap. Fix: SQS between S3 and Lambda with a DLQ + `ReportBatchItemFailures`, OpenSearch backoff, reconciliation job via S3 Inventory.

### Drill 6: "Cross-account `AccessDenied` reading an S3 object."
Follow the chain: caller identity → identity policy → bucket policy → object ownership (an object uploaded by another account with ACLs disabled? owner enforced?) → KMS key policy + grants → VPC endpoint policy → SCP/RCP. The error message tells you which policy type denied.

### Drill 7: "After a regional failover drill, the DR region was up but p99 was 10× for 40 minutes."
Cold caches, an EBS lazy-loading restore, JVM warm-up, connection pool storms against the promoted DB, autoscaling lag from low pre-provisioning, DNS TTLs keeping some clients on the old region. Fix: pre-warm (EVCache-style replication), Fast Snapshot Restore, a progressive traffic ramp, pre-scaled capacity, client connection limits.

### Drill 8: "Bedrock agent sometimes hangs for 60s and then fails."
Tool call to a slow API without a timeout; model throttling with long SDK retries; a streaming connection idle-timed out by an intermediate LB (ALB idle timeout 60s!); a loop with no max iterations. Check traces (AgentCore Observability spans), set per-tool timeouts, raise LB idle timeout or use streaming keep-alives, and add budgets.

### Drill 9: "CloudWatch bill tripled."
Cost Explorer → usage type: `DataProcessing-Bytes` (Logs ingestion) → which log groups (`IncomingBytes` metric per log group) → a debug flag left on, or a new high-volume access log. `MetricMonitorUsage` → a cardinality explosion from a new dimension. Fix and add Cost Anomaly Detection.

### Drill 10: "Terraform apply wants to destroy and recreate the production database."
STOP. Look at the plan: the attribute forcing replacement (e.g. a changed `identifier`, `availability_zone`, engine version semantics, or a provider upgrade). Use `lifecycle { prevent_destroy = true; ignore_changes = [...] }`, a `moved` block for refactors, or `import` blocks. Never apply. Add a policy check that fails plans with deletes on stateful resources.

<!-- nav -->
---

← [Senior AWS Question Bank (as asked when hiring someone with ~10 years of experience)](01-senior-question-bank.md) · [🏠 Home](../README.md) · [Hands-On Labs](../06-labs/README.md) →
