# Senior AWS Question Bank (as asked when hiring someone with ~10 years of experience)

How to use this: for each question, answer **out loud in 2–3 minutes**, then open the linked chapter. A strong senior answer has four parts: **mechanism** (how it works), **trade-offs**, **failure modes**, **how you operate it** (metrics, alarms, runbooks). Questions are grouped by theme; ⭐ = asked very often.

---

## A. Fundamentals under the hood
1. ⭐ Control plane vs data plane: give three examples where depending on the control plane during recovery fails. → [Global infra](../01-foundations/01-global-infrastructure.md)
2. What is static stability? Redesign this to be statically stable: "ASG min=2, max=20, 2 AZs, scales on CPU 70%".
3. Why 3 AZs? Do the capacity maths for N = 2, 3, 4.
4. AZ name vs AZ ID. Why does it matter for PrivateLink and multi-account designs?
5. Which "global" services have their control plane in us-east-1, and how does that change your DR plan?

## B. IAM & security
6. ⭐ Walk through policy evaluation for a cross-account read of an SSE-KMS object. Name all 4+ policies involved. → [IAM](../01-foundations/02-iam-deep-dive.md)
7. ⭐ SCP vs RCP vs permission boundary vs session policy.
8. Confused deputy: explain and fix for (a) a SaaS vendor, (b) SNS → SQS.
9. IRSA vs EKS Pod Identity: how each gets credentials; scaling limits.
10. Build a data perimeter: identity, resource, network. Which condition keys?
11. ⭐ A leaked access key is found. Full runbook.
12. Envelope encryption and encryption context; why KMS has a 4 KB limit.
13. How do you give 50 teams IAM self-service without privilege escalation? → boundaries + `iam:PassRole` conditions.

## C. Networking
14. ⭐ SG vs NACL: show a NACL breaking return traffic. → [VPC](../01-foundations/03-vpc-networking.md)
15. ⭐ NAT Gateway bill is huge: diagnose and fix.
16. Peering vs TGW vs Cloud WAN vs PrivateLink vs VPC Lattice: pick for 5 scenarios.
17. Overlapping CIDRs after an acquisition: options.
18. Hybrid DNS with Route 53 Resolver endpoints: walk through both directions.
19. Why does a stateful firewall in an inspection VPC need TGW appliance mode?
20. Connection tracking limits: symptoms and how to confirm.

## D. Compute & containers
21. ⭐ ALB 502s during deployments: root causes. → [Compute](../01-foundations/04-compute-and-load-balancing.md)
22. Lambda cold start end to end; SnapStart vs provisioned concurrency.
23. Kinesis → Lambda iterator age rising: every lever.
24. ECS vs EKS for a 40-service org: defend the choice by operating model.
25. EKS pod IP exhaustion: three fixes.
26. Spot strategy for a mixed workload; what are capacity-rebalance and price-capacity-optimized?

## E. Storage & databases
27. S3 strong consistency: what's guaranteed, and what still isn't atomic? → [Storage](../01-foundations/05-storage.md)
28. S3 503 Slow Down: why, and fixes.
29. ⭐ Aurora storage architecture: 6 copies, 4/6 write quorum, 10 GB segments. Why? → [Databases](../01-foundations/06-databases.md)
30. Aurora failover takes 2 min in prod but 30s in tests: why?
31. ⭐ DynamoDB hot partition: causes and fixes; adaptive capacity limits.
32. Single-table design for an order system: show PK/SK/GSI.
33. Global tables LWW conflicts: how do you design around them? MRSC trade-offs.
34. Cache stampede after a Redis failover: prevention.
35. Zero-downtime major PostgreSQL upgrade on RDS.

## F. Messaging & integration
36. ⭐ Idempotent consumer design with SQS + DynamoDB. → [Messaging](../01-foundations/07-messaging-and-events.md)
37. Visibility timeout vs processing time; heartbeat pattern.
38. Transactional outbox on AWS.
39. Draining a 50M message backlog without killing the DB.
40. Orchestration (Step Functions) vs choreography (EventBridge).

## G. Reliability & operations
41. ⭐ Timeouts, retries, backoff, jitter, retry budgets: explain retry amplification. → [Resilience](../02-operating-aws/04-resilience-and-dr.md)
42. ⭐ What is a metastable failure? Use a real AWS outage as an example. → [Outages](../03-architecture-deep-dives/06-aws-outage-postmortems.md)
43. Pick DR strategies per tier for a bank: RTO/RPO, costs.
44. ⭐ SLO → burn-rate alarms in CloudWatch. → [Observability](../02-operating-aws/01-observability.md)
45. How would you run chaos engineering in production safely? (ChAP / FIS stop conditions)
46. Shallow vs deep health checks; LB fail-open behaviour.
47. Grey failure in one AZ: detect and mitigate (zonal shift).
48. Load shedding vs autoscaling interplay. → [Netflix load shedding](../03-architecture-deep-dives/02-netflix-traffic-and-load-shedding.md)

## H. Delivery & IaC
49. ⭐ Design a multi-region deployment pipeline like AWS's (one-box → AZ → region → waves). → [IaC](../02-operating-aws/02-iac-and-deployments.md)
50. Rollback safety with data format changes (two-phase deploy).
51. CloudFormation `UPDATE_ROLLBACK_FAILED`: recovery.
52. Automated canary analysis: why a fresh baseline; which statistical test?
53. Terraform state management at scale.

## I. Governance, cost, org scale
54. ⭐ Landing zone for 300 accounts: OUs, SCPs, logging, identity. → [Security](../02-operating-aws/03-security-and-governance.md)
55. How do you test SCPs safely?
56. ⭐ The bill grew 40% with flat traffic: investigation. → [Cost](../02-operating-aws/05-cost-engineering.md)
57. Savings Plans strategy for $10M/year.
58. Five surprising AWS costs.

## J. Architecture / system design
59. ⭐ Multi-region active-active for a streaming control plane. → [Netflix multi-region](../03-architecture-deep-dives/01-netflix-multi-region-active-active.md)
60. ⭐ Cell-based architecture and shuffle sharding with numbers. → [Cells](../03-architecture-deep-dives/05-cells-and-shuffle-sharding.md)
61. Distributed counter at millions of increments/sec. → [Netflix data](../03-architecture-deep-dives/03-netflix-data-platform.md)
62. Flash sale without overselling. → [Reference architectures](../03-architecture-deep-dives/07-reference-architectures.md)
63. Multi-tenant SaaS isolation models (silo/pool/bridge/cells).
64. Video processing pipeline on Spot.
65. Data platform: why abstract the database behind a gateway (Netflix DAL)?

## K. GenAI / Bedrock / agents
66. ⭐ Bedrock throttling: all the levers. → [Bedrock](../04-genai-bedrock/01-bedrock-internals.md)
67. Geographic vs global CRIS + SCP implications.
68. Prompt caching mechanics and the cost maths for an agent loop.
69. ⭐ Permissions-aware RAG over 20M documents. → [RAG](../04-genai-bedrock/02-rag-and-knowledge-bases.md)
70. Debug bad RAG answers: retrieval vs generation evaluation.
71. ⭐ AgentCore Runtime isolation; Identity OAuth on behalf of the user; Policy with Cedar. → [Agents](../04-genai-bedrock/03-agents-and-agentcore.md)
72. Indirect prompt injection defence for a tool-using agent.
73. AI gateway for 10 teams: quotas, chargeback, resilience. → [GenAI platform](../04-genai-bedrock/04-production-genai-platform.md)

## L. Behavioural (senior/staff signals)
74. Tell me about the worst production incident you led. Timeline, decisions, what changed after.
75. A time you pushed back on an architecture: what data did you use?
76. How did you reduce cost without hurting reliability?
77. How do you make a platform that 50 teams *want* to adopt (paved road vs mandates)?

> Tip: use the STAR format, but spend most of the time on *the technical decisions and trade-offs*. Senior interviewers probe "why not X?" Always have a rejected alternative ready.

<!-- nav -->
---

← [GenAI 04 · Building a Production GenAI Platform on AWS (Multi-Team, Multi-Account)](../04-genai-bedrock/04-production-genai-platform.md) · [🏠 Home](../README.md) · [Troubleshooting Drills (Scenario Interviews)](02-troubleshooting-drills.md) →
