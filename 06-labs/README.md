# Hands-On Labs

Reading isn't enough. Interviewers can tell who has *operated* AWS. These labs are designed to be cheap (mostly free tier or a few dollars) if you **tear down** after each one.

> **Before you start:** use a dedicated sandbox account under AWS Organizations, set a **Budget alarm at $20**, enable MFA, and never use the root user. Destroy resources after each lab (`cdk destroy` / `terraform destroy`).

| # | Lab | Teaches | Est. cost |
|---|---|---|---|
| 1 | **Account foundation**: Organizations, Identity Center user, SCP region deny, CloudTrail org trail, Budget | IAM, governance | ~$0 |
| 2 | **VPC from scratch** (Terraform): 3 AZs, public/private subnets, NAT per AZ, S3 gateway endpoint, Flow Logs → S3 + Athena query | Networking, cost | ~$3/day (NAT) |
| 3 | **Break the NACL**: block ephemeral ports, observe the failure, fix it; Reachability Analyzer | Debugging | ~$0 |
| 4 | **ALB + ASG across 3 AZs**: target tracking, lifecycle hook, rolling instance refresh; reproduce 502s by killing the app without draining, then fix with graceful shutdown | Compute, deploys | ~$2 |
| 5 | **FIS AZ experiment**: run "AZ power interruption" (or stop instances in one AZ) with a stop condition on a 5xx alarm; try zonal shift on the ALB | Resilience | ~$1 |
| 6 | **Serverless order API**: API Gateway → Lambda → DynamoDB single-table; SQS + idempotency (Powertools); DLQ redrive | Event-driven | ~$0 |
| 7 | **DynamoDB hot key**: load-test a single PK, watch throttling + Contributor Insights, fix with write sharding | Data modelling | ~$1 |
| 8 | **Aurora failover**: Aurora Serverless v2 with a reader; run a client loop; `failover-db-cluster`; measure downtime with and without the AWS JDBC wrapper / RDS Proxy | Databases | ~$3 |
| 9 | **SLO burn-rate alarms**: CloudWatch metric math + composite alarm from lab 4/6 metrics | Observability | ~$0 |
| 10 | **Cross-account S3 + KMS**: two accounts, bucket policy + key policy + role; reproduce each AccessDenied and fix | IAM depth | ~$1 |
| 11 | **CI/CD with OIDC**: GitHub Actions → AWS role (no keys), CDK deploy, CodeDeploy canary with auto-rollback alarm | Delivery | ~$0 |
| 12 | **Bedrock basics**: Converse + tool use + prompt caching (measure cache-read tokens) + Guardrail with PII masking | GenAI | ~$1 |
| 13 | **RAG**: Knowledge Base over your own docs (S3 Vectors or OpenSearch Serverless; note OSS minimum cost), metadata filters, evaluation job | GenAI | ~$2–10 |
| 14 | **Agent on AgentCore**: Strands agent → AgentCore Runtime; Gateway with one OpenAPI tool; a Cedar policy that denies a parameter value; view traces in CloudWatch | Agents | ~$2 |
| 15 | **Capstone**: two-region active/passive with DynamoDB global tables + ARC routing control; run a failover drill and write a mini post-incident report | Everything | ~$5 |

### Capstone report template
```
Objective / hypothesis:
Setup (diagram):
Steps & timestamps:
Measured RTO / RPO:
What broke / surprised you:
Fixes & follow-ups:
```
Put your lab code and reports in your own GitHub repo. It's a strong portfolio signal in interviews.
