# Deep Dive 04 · Netflix Delivery, Chaos Engineering & Cloud Security

> **Interviewer lens:**
> - "How would you let 2,000 engineers deploy to production hundreds of times a day, safely?"
> - "Explain automated canary analysis statistically. Why compare canary to a *new* baseline, not to prod?"
> - "How do you run chaos experiments in production without hurting customers?"
> - "Instance credentials were stolen via SSRF and used from outside AWS. How do you detect that?"
> - "How do you get least privilege across thousands of IAM roles that engineers over-provisioned?"

---

## Part A: Continuous delivery

### A1. The paved road
Netflix's culture is "freedom and responsibility": teams own their services end to end. The platform makes the safe way the easy way:
- **Spinnaker** (open-sourced 2015): multi-cloud CD platform. Pipelines bake an **immutable AMI** (or container image), then deploy as a new **server group** (ASG), red/black (blue/green) or rolling, across regions.
- **Immutable infrastructure**: never patch running instances. Bake new images and replace.
- **Titus** (open-sourced 2018): Netflix's container platform on EC2 (batch + services), integrated with VPC networking (an IP per container), IAM per container via a metadata proxy, and Spinnaker.

### A2. Red/black (blue/green) the Netflix way
```
Server group v041 (old, 100% traffic)
Spinnaker: create v042 (same size) → health checks → enable v042 in LB → disable v041 (keep it running!)
If metrics regress → re-enable v041, disable v042 (rollback in seconds, no new launches)
After N hours → destroy v041
```
This is **static stability applied to deploys**: rollback needs no new capacity.

### A3. Automated canary analysis: Kayenta (2018, with Google)
```
Production fleet (v1)        ← not used for comparison
Baseline cluster (v1, NEW, 3 instances)   ← equal small traffic share
Canary cluster   (v2, NEW, 3 instances)   ← equal small traffic share
Kayenta: pull metrics for both (Atlas, Prometheus, CloudWatch, Datadog…)
         per metric: Mann-Whitney U test → pass / high / low
         weighted groups (errors > latency > CPU) → score 0–100
         score ≥ pass threshold → promote; ≤ marginal → fail & roll back
```
**Why a fresh baseline?** Long-running prod instances differ from new ones (warm JIT, full caches, memory fragmentation, accumulated leaks). Comparing a fresh canary to old prod measures *age*, not *code*.

**Why Mann-Whitney?** It's non-parametric. Latency distributions aren't normal, and it compares the whole distribution, not just the mean.

On AWS without Spinnaker: CodeDeploy canary + CloudWatch alarms (simplest), **Argo Rollouts** with AnalysisTemplates (Prometheus queries), or Flagger. Automated statistical comparison is the part teams usually skip, and it's the valuable part.

---

## Part B: Chaos engineering

### B1. Evolution
| Tool | Year | What it does |
|---|---|---|
| **Chaos Monkey** | 2011 | Randomly terminates instances in production during business hours, so engineers build for instance loss |
| **Simian Army** | 2011–2012 | Latency Monkey, Conformity Monkey, Janitor Monkey, **Chaos Gorilla** (AZ), **Chaos Kong** (region) |
| **FIT** (Failure Injection Testing) | 2014 | Request-level fault injection: a header tags requests from specific users/devices to fail or slow a specific dependency. Precise blast radius |
| **ChAP** (Chaos Automation Platform) | 2017 | Automated experiments on a small % of real traffic with **control vs experiment** clusters; auto-stop on KPI deviation |
| Monocle / continuous experimentation | 2018+ | Automatically generates experiments from dependency graphs; runs them continuously |

### B2. ChAP design (how to do chaos safely)
```
Real traffic ─► Zuul ─► (tiny %) ─┬─► Control cluster    (new, same version, no faults)
                                  └─► Experiment cluster (new, same version, FAULT injected via FIT)
Compare SPS / error rates between the two populations of users.
Deviation beyond threshold → STOP immediately (automated), roll back routing.
```
Key safety properties:
1. **Blast radius**: a tiny traffic percentage; specific dependencies.
2. **Control group**: compare like with like, at the same time, so you're not fooled by daily patterns.
3. **Business KPI as the steady-state metric** (SPS), not CPU.
4. **Automatic abort.**
5. Run during business hours with owners aware.

### B3. On AWS: Fault Injection Service (FIS)
- Actions: stop/terminate instances, **AZ power interruption** scenario, network disruption (subnet/NACL-based), API throttle/errors injection for IAM roles, EKS pod faults, ECS task faults, RDS failover, Lambda faults.
- **Stop conditions** = CloudWatch alarms (your SLO burn alarm). This is AWS's version of ChAP's auto-abort.
- Use targets selected by tags and percentages to limit blast radius.
- Start with game days in staging, then small production experiments.

---

## Part C: Cloud security at Netflix

### C1. Detecting stolen instance credentials (2018)
Threat: an attacker steals an EC2 instance's role credentials (SSRF → IMDS, like the Capital One breach) and uses them **from outside** your environment.

Netflix's approach:
1. Build an inventory of which IPs belong to their instances (EC2 public IPs, NAT/EIPs) over time.
2. Process **CloudTrail**. For each API call made with an instance role session, check whether `sourceIPAddress` belongs to Netflix's infrastructure (or is an AWS service calling on its behalf).
3. Calls from unknown IPs → alert (credential compromise).

AWS today: **GuardDuty** `UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration.OutsideAWS` and `.InsideAWS` findings do this natively. **Prevent** it with IMDSv2 (hop limit 1), and the condition keys `aws:EC2InstanceSourceVPC` / `aws:EC2InstanceSourcePrivateIPv4` (data-perimeter SCP: deny if instance creds are used outside their VPC).

### C2. Least privilege at scale: Repokid & Aardvark
- **Aardvark** collects "last accessed" data from IAM Access Advisor for every role.
- **Repokid** automatically **removes unused permissions** from roles (after a learning period and with notifications), then keeps doing it. Engineers start with a broad default role, and Repokid shrinks it to what the service actually uses.
- AWS today: **IAM Access Analyzer** unused-access findings + policy generation from CloudTrail; service last-accessed data via API.

### C3. ConsoleMe & Weep (2020)
- Central web app for engineers to get console/CLI credentials for the roles they're allowed, request new permissions with self-service policy editing and approvals, and keep it auditable.
- AWS today: IAM Identity Center + permission sets + TEAM (temporary elevated access).

### C4. Other Netflix security tools
- **BLESS**: SSH certificate authority running in Lambda (short-lived SSH certs instead of static keys). AWS today: SSM Session Manager (no SSH at all) / EC2 Instance Connect.
- **Lemur**: TLS certificate management. AWS today: ACM + Private CA.
- **Security Monkey** (retired): config monitoring. AWS today: AWS Config + Security Hub.
- **Metatron**: workload identity / mTLS credentials for services.

---

## Interview drill

<details><summary><b>Q: "Design guardrails so 2,000 engineers can deploy hundreds of times per day."</b></summary>
A paved-road pipeline (template) that's the default for every new service: build → test → bake an immutable image → deploy to canary with automated canary analysis vs a fresh baseline → staged regional rollout (one region first) → red/black so rollback is instant → automatic rollback on SLO burn. Platform-owned libraries for IPC, retries, metrics and tracing so every service exposes the same signals. Self-service, no tickets. Guardrails, not gates: policy-as-code blocks dangerous changes (public S3, wildcard IAM), but humans don't approve routine deploys. Measure DORA metrics.
</details>

<details><summary><b>Q: "Our CTO wants chaos engineering. Where do you start?"</b></summary>
Prerequisites first: good observability with a business KPI, tested rollback, on-call runbooks. Then (1) game days in staging: kill instances, fail an AZ with FIS, inject dependency latency. (2) Fix what breaks. (3) Small production experiments with FIS stop conditions on SLO alarms, during business hours. (4) Automate recurring experiments (instance termination, AZ evacuation via zonal shift). (5) Region evacuation drills for tier-0. Report findings as reliability work items with owners.
</details>

## Further reading
- Netflix Tech Blog: *Global Continuous Delivery with Spinnaker* (2015); *Automated Canary Analysis at Netflix with Kayenta* (2018); *Titus, the Netflix container management platform, is now open source* (2018).
- Netflix Tech Blog: *The Netflix Simian Army* (2011); *FIT: Failure Injection Testing* (2014); *ChAP: Chaos Automation Platform* (2017).
- Book: *Chaos Engineering* (Casey Rosenthal & Nora Jones, O'Reilly), written by ex-Netflix chaos team members.
- Netflix Tech Blog: *Netflix Cloud Security: Detecting Credential Compromise in AWS* (2018); *Introducing ConsoleMe* (2020); GitHub `Netflix/repokid`, `Netflix-Skunkworks/aardvark`.
