# Certification Map: SAP-C02 (Solutions Architect – Professional) & DOP-C02 (DevOps Engineer – Professional)

You chose the Professional track. Realistic path for a software engineer new to AWS:
1. Work through **01-foundations** + labs 1–10 (6–8 weeks).
2. *(Optional but recommended)* Sit **SAA-C03 (Associate)** as a checkpoint. It's not a prerequisite, but the Professional exams assume that level.
3. Then **SAP-C02** (architecture breadth + org scale) and/or **DOP-C02** (operations + automation).

> Exam guides change. Check the current domain weights in the official exam guide on aws.amazon.com/certification before you book.

---

## SAP-C02 domains → chapters

| Domain (approx. weight) | Chapters |
|---|---|
| **1. Design solutions for organizational complexity** (~26%) | IAM §7, Security & governance (landing zone, SCP/RCP), VPC §3 (TGW, Cloud WAN, hybrid DNS), Cost §4 |
| **2. Design for new solutions** (~29%) | All of 01-foundations, Resilience & DR, Cells & shuffle sharding, Reference architectures, Bedrock chapters |
| **3. Continuous improvement for existing solutions** (~25%) | Observability, IaC & deployments, Cost engineering, Netflix deep dives (load shedding, data platform) |
| **4. Accelerate workload migration and modernization** (~20%) | *Add:* Migration Hub/Transform, Application Migration Service (MGN), DMS + SCT, DataSync, Snow Family, 7 Rs (rehost, replatform, refactor, repurchase, relocate, retain, retire) |

**SAP-C02 tips:** questions are long, multi-paragraph scenarios. Look for the *constraint words*: "least operational overhead", "most cost-effective", "minimize downtime", "without changing the application". Eliminate answers that violate the constraint first.

## DOP-C02 domains → chapters

| Domain | Chapters |
|---|---|
| **1. SDLC automation** | IaC & deployments (CodePipeline/CodeBuild/CodeDeploy strategies, canary, artifact signing) |
| **2. Configuration management & IaC** | IaC (CFN, CDK, StackSets, drift), Security (Control Tower, SSM) |
| **3. Resilient cloud solutions** | Resilience & DR, Global infra (static stability), Databases (Aurora Global, DDB global tables) |
| **4. Monitoring & logging** | Observability (CloudWatch, EMF, Logs Insights, CloudTrail, Config) |
| **5. Incident & event response** | Security §4 IR, EventBridge + SSM Automation, Troubleshooting drills |
| **6. Security & compliance** | IAM, Security & governance, KMS, Secrets Manager |

**DOP-C02 tips:** know the *exact* behaviour of CodeDeploy deployment configs (EC2 vs ECS vs Lambda), CloudFormation StackSets (service-managed vs self-managed permissions), SSM (Patch Manager, State Manager, Automation runbooks), and EventBridge → remediation patterns.

## Extra topics to cover only for the exams (lighter in this guide)
- Migration services (MGN, DMS, DataSync, Transfer Family, Snowball), Storage Gateway.
- Elastic Beanstalk, OpsWorks history (mostly legacy now).
- Workspaces, AppStream (occasionally appear).
- Detailed CodeArtifact/CodeGuru features.

## Study resources
- Official AWS Skill Builder exam prep and practice question sets.
- AWS whitepapers: Well-Architected Framework (6 pillars) + lenses (Serverless, SaaS, **Generative AI lens**), *AWS Fault Isolation Boundaries*, *Disaster Recovery of Workloads on AWS*, *Organizing Your AWS Environment Using Multiple Accounts*.
- Practice exams from reputable providers. Aim for 80%+ consistently before booking.

<!-- nav -->
---

← [Hands-On Labs](../06-labs/README.md) · [🏠 Home](../README.md)
