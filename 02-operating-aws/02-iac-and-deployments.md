# 02 · Infrastructure as Code & Safe Deployments

> **Interviewer lens.**
> - "CloudFormation vs CDK vs Terraform: what breaks at scale with each?"
> - "A CloudFormation stack is stuck in `UPDATE_ROLLBACK_FAILED`. What do you do?"
> - "How does Amazon deploy to 30+ regions without a global outage? Design that pipeline for us."
> - "Blue/green vs canary vs rolling vs feature flags: when each?"
> - "How do you make a deployment *rollback-safe* when the change includes a data-format change?"
> - "How do you detect and handle drift?"

---

## 1. IaC tools compared

| | CloudFormation | CDK | Terraform / OpenTofu |
|---|---|---|---|
| Language | YAML/JSON | TS/Python/Java/Go/C# → synthesizes CFN | HCL |
| State | Managed by AWS (stack) | Same as CFN | **State file** you manage (S3 + locking; S3 native lockfile now) |
| Multi-cloud | No | No | Yes |
| Rollback | Automatic on failure | Same | None. Partial applies leave partial state |
| New AWS feature support | Usually day 1 (sometimes lags) | Same, plus L2 constructs later | Provider releases (often fast) |
| Drift | Drift detection | Same | `plan` shows drift |
| Scale pain | 500 resources/stack limit, slow stacks, nested stack complexity | Abstraction can hide what gets deployed. Always review `cdk diff` | State-file blast radius, module sprawl, provider upgrades |

**Senior take:** the tool matters less than the **structure**:
- Small stacks by **lifecycle and blast radius** (network, data, compute, app). Data stores in their own stack with `DeletionPolicy: Retain` / `prevent_destroy`.
- One pipeline per stack/environment; never apply from laptops to prod.
- Policy-as-code in CI: cfn-guard / Checkov / OPA, Access Analyzer policy checks, plus **CloudFormation Hooks** or Control Tower proactive controls that block non-compliant resources at deploy time.

### `UPDATE_ROLLBACK_FAILED`
Rollback couldn't restore a resource (e.g. someone deleted it manually, or a dependency changed). Fix the underlying cause, then `ContinueUpdateRollback`, optionally with `--resources-to-skip`. Prevention: no manual changes (SCP-deny console writes in prod), drift detection, and **stack policies** to protect critical resources.

---

## 2. How AWS deploys (the pattern to copy)

From the Builders' Library *Automating safe, hands-off deployments*:
```
Commit → build → unit tests → pre-prod (alpha/beta/gamma, integration tests)
  → ONE-BOX in first prod region (a single host/cell) ── bake, watch alarms
  → first AZ of first region (small region first) ── bake
  → rest of region ── bake
  → WAVE 2: a few more regions (still not all) ── bake
  → WAVE 3..N: progressively larger waves
  Automatic rollback on alarm at every stage. Never deploy to two regions' "first AZ" at once.
  Bake times shrink as confidence grows. Deploys pause outside business hours/holidays.
```
Principles:
- **Blast radius limiting:** cell → AZ → region → waves.
- **Automated rollback** on the metrics customers care about, *plus* new-code-specific metrics.
- **Rollback safety:** every change must be able to coexist with the previous version (see §4).
- **Deploying config is deploying code.** The 2017 S3 outage was a mistyped *command*; the 2025 DynamoDB event was *automated DNS changes*. Config/automation changes need the same staging, validation and rate limits.

---

## 3. Deployment strategies

| Strategy | How on AWS | Pros | Cons |
|---|---|---|---|
| Rolling | ECS rolling, ASG instance refresh, EKS Deployment | Cheap | Mixed versions; slow rollback |
| **Blue/green** | CodeDeploy (ECS/Lambda/EC2), ALB weighted target groups, ECS native blue/green, Route 53 weighted | Instant rollback (flip back) | 2× capacity during the deploy; DB schema must be compatible |
| **Canary** | CodeDeploy `Canary10Percent5Minutes`, Lambda alias weights, Argo Rollouts, **Kayenta** (Netflix/Google automated canary analysis) | Real traffic validation, small blast radius | Needs good metrics and statistical comparison |
| Linear | CodeDeploy `Linear10PercentEvery1Minute` | Gradual | Slow |
| Feature flags | AppConfig feature flags, LaunchDarkly | Decouple deploy from release; kill switch | Flag debt |
| Shadow / dark traffic | Mirror requests (VPC Traffic Mirroring, app-level) | Zero user risk | Side effects must be suppressed |

### Automated canary analysis (Netflix Kayenta / Spinnaker)
Run **baseline** (old version, fresh instances) and **canary** (new version) side by side with equal small traffic. Compare dozens of metrics statistically (Mann-Whitney U test) → score → pass/fail. Using a fresh baseline instead of the existing prod fleet avoids bias from long-running instances (warm caches, memory leaks). See [04-netflix-delivery-chaos-and-security.md](../03-architecture-deep-dives/04-netflix-delivery-chaos-and-security.md).

---

## 4. Rollback safety: the two-phase change

Problem: v2 writes a new data format. If you roll back to v1, v1 can't read what v2 wrote, so rollback **breaks** things.

**Solution (expand/contract, a.k.a. two-phase deployment):**
1. **Phase 1 (reader):** deploy code that can *read* both old and new formats but still *writes* old. Fully deploy everywhere.
2. **Phase 2 (writer):** deploy code that writes the new format. Rollback to Phase 1 is safe, since it can read both.
3. **Contract:** after a while, remove old-format support.

DB schema version: add column (nullable) → backfill → app writes both → app reads new → drop old column later. Never rename in one step.

---

## 5. Multi-account CI/CD layout
```
Tooling account: CodePipeline / GitHub Actions runners / Argo CD
   │ assumes deploy roles (OIDC, no static keys) ─┬─► dev account
   │                                               ├─► staging account
   │                                               └─► prod accounts (per region waves)
Artifacts: ECR (cross-account pull policy, image scanning, signing w/ AWS Signer/Notation)
           S3 artifact bucket with KMS key shared to target accounts
```
- Prod deploy roles trust **only** the pipeline role, gated by SCP: "only `DeployRole` can modify CFN stacks in prod".
- Supply chain: SBOMs, image signing + admission verification (Kyverno / Gatekeeper on EKS), Inspector scanning, pinned base images.

---

## 6. Drift
- CloudFormation drift detection (scheduled via Config rule `cloudformation-stack-drift-detection-check`).
- Terraform: scheduled `plan -detailed-exitcode` in CI → alert on exit code 2.
- **Prevention beats detection:** read-only console roles in prod; break-glass with alerts.

---

## 7. Senior interview questions — model answers

<details><summary><b>Q1. Design a deployment pipeline for a service in 6 regions with 99.99% SLO.</b></summary>

Trunk-based, every merge deploys. Stages: build/test → integration in staging → prod wave 1 = one cell/one-box in the lowest-traffic region with ~1h bake + automated canary analysis → full region 1 → wave 2 = 2 regions in parallel (never both members of a failover pair at once) → wave 3 = rest. Auto-rollback on SLO burn, error and latency alarms, and on canary score. Freeze windows. Config changes through AppConfig with the same staged rollout and validators. Data migrations use expand/contract. The design keeps any bad change to ≤1 region's partial capacity, inside the error budget.

</details>

<details><summary><b>Q2. Terraform state got corrupted / someone applied an old plan. Recovery?</b></summary>

S3 versioning on the state bucket → restore the previous version. Locking (DynamoDB table or S3 native locking) should have prevented concurrent applies. Use `terraform import` / `import` blocks to re-adopt resources, `state rm` for orphans, then `plan` to converge. Prevention: CI-only applies, plan-then-apply with the saved plan file, short-lived state access, separate state per stack/env.

</details>

<!-- nav -->
---

← [01 · Observability: Knowing What's Broken Before Customers Do](01-observability.md) · [🏠 Home](../README.md) · [03 · Security & Governance at Organization Scale](03-security-and-governance.md) →
