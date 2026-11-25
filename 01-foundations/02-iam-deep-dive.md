# 02 · IAM Deep Dive: How AWS Decides "Allow" or "Deny"

> **Interviewer lens.** IAM is where senior candidates get separated from people who only clicked through the console:
> - "Walk me through the policy evaluation logic when a role in account A reads a KMS-encrypted S3 object in account B."
> - "SCP, permission boundary, session policy, identity policy, resource policy: which ones *grant* and which only *limit*?"
> - "What is the confused-deputy problem and how does `aws:SourceArn` / `ExternalId` fix it?"
> - "How does a pod in EKS get AWS credentials? Compare IRSA and EKS Pod Identity."
> - "How would you find and remove unused permissions across 300 accounts?"
> - "A developer gets `AccessDenied`. How do you debug it systematically?"

---

## 1. The mental model: every API call is a signed request + a decision

```
Client ──SigV4-signed HTTPS──► AWS service front end
                                 │ 1. Authenticate: verify signature with the secret key
                                 │    that belongs to the access key ID (long-term or STS temp)
                                 │ 2. Build the REQUEST CONTEXT:
                                 │    principal, action, resource, condition keys
                                 │    (aws:SourceIp, aws:PrincipalOrgID, aws:RequestedRegion,
                                 │     aws:PrincipalTag/*, s3:prefix, kms:ViaService …)
                                 │ 3. Gather every applicable policy
                                 │ 4. Evaluate → Allow / Deny
                                 ▼
```
- **SigV4** signs the method, path, headers and body hash with a key derived from your secret: `HMAC(date) → region → service → "aws4_request"`. The secret never travels on the wire. A signature is valid for about 5 minutes (protects against replay). SigV4a (asymmetric) is used for multi-region endpoints such as S3 Multi-Region Access Points.
- **Temporary credentials** (from STS) are access key + secret + **session token**. Every AWS SDK resolves credentials through a chain: env vars → shared config/SSO → web identity token (IRSA) → container credentials endpoint (ECS/Pod Identity) → EC2 IMDS.

---

## 2. Policy types: who grants, who only limits

| Policy type | Attached to | Can **grant**? | Purpose |
|---|---|---|---|
| **Identity-based** | User / group / role | ✅ | What this principal can do |
| **Resource-based** | Bucket, KMS key, SQS queue, Lambda, role trust policy … | ✅ (incl. cross-account) | Who can touch this resource |
| **SCP** (Organizations) | OU / account | ❌ only limits | Max permissions for every principal in the account (not the management account) |
| **RCP** (Resource Control Policy) | OU / account | ❌ only limits | Max permissions **on resources** in the account, whoever the caller is (e.g. "no one outside my org can read my S3") |
| **Permission boundary** | User / role | ❌ only limits | Max permissions a delegated admin can hand out |
| **Session policy** | Passed at `AssumeRole` | ❌ only limits | Scope down one session |
| **ACLs** (S3 legacy) | Bucket/object | ✅ | Avoid. Disable via *Bucket owner enforced*. |

### The evaluation algorithm (memorize this)
```
1. Explicit DENY anywhere?                                  → DENY   (always wins)
2. SCPs (every level from root to account) allow it?        → if not: DENY
3. RCPs allow it?                                           → if not: DENY
4. Resource-based policy allows it?
     - same account & principal named directly → ALLOW (can skip identity policy*)
5. Identity-based policy allows it?                         → if not: DENY (implicit)
6. Permission boundary allows it?                           → if not: DENY
7. Session policy allows it?                                → if not: DENY
→ ALLOW
```
\* Same-account nuance: a resource policy that names the principal can allow the call even when no identity policy allows it. Whether permission boundaries and session policies still cut it down depends on *which* ARN the policy names: user, role, or role-session. That edge case is in the docs, and interviewers love it. Explicit denies and SCPs always apply. KMS key policies and IAM role trust policies are special. They must explicitly allow, even within the same account (a KMS key policy must delegate to IAM with `"Principal": {"AWS": "arn:aws:iam::111122223333:root"}` before identity policies have any effect).

### Cross-account = **both sides must say yes**
```
Account A role ──(identity policy allows s3:GetObject on B's bucket)──┐
                                                                      ▼  both required
Account B bucket ─(bucket policy allows principal A-role)─────────────┘
If the object is SSE-KMS: B's KMS key policy must ALSO allow A-role kms:Decrypt,
and A-role's identity policy must allow kms:Decrypt on that key ARN.
```
This 4-policy chain is the single most common senior-interview IAM question.

---

## 3. Roles and STS: how trust works

A **role** has two policies:
1. **Trust policy** (resource policy on the role): *who* may call `sts:AssumeRole*`.
2. **Permission policies**: *what* the session can do.

| STS API | Who calls it | Typical use |
|---|---|---|
| `AssumeRole` | An AWS principal | Cross-account access, CI pipelines |
| `AssumeRoleWithWebIdentity` | Holder of an OIDC JWT | GitHub Actions OIDC, IRSA in EKS |
| `AssumeRoleWithSAML` | SAML assertion | Enterprise SSO (legacy; prefer IAM Identity Center) |
| `GetSessionToken` | IAM user | MFA-protected sessions |

**Role chaining** (role assumes another role) caps the session at **1 hour**.

### GitHub Actions → AWS without secrets
```json
{
  "Effect": "Allow",
  "Principal": {"Federated": "arn:aws:iam::111122223333:oidc-provider/token.actions.githubusercontent.com"},
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
    "StringLike":   {"token.actions.githubusercontent.com:sub": "repo:my-org/my-repo:ref:refs/heads/main"}
  }
}
```
**Classic mistake:** omitting or wild-carding `sub` (`repo:*`) lets *any* GitHub repo assume your role. This has caused real breaches.

---

## 4. The confused-deputy problem

A "deputy" (an AWS service or a SaaS vendor) holds permission to act on your behalf. An attacker tricks the deputy into using *your* permissions for *their* request.

| Scenario | Fix |
|---|---|
| SaaS vendor assumes a role in your account | Trust policy requires `sts:ExternalId` that is unique per customer and generated by the vendor |
| AWS service (e.g. SNS → SQS, S3 → Lambda) writes to your resource | Resource policy condition `aws:SourceArn` / `aws:SourceAccount` |
| Org-wide guardrail | `aws:PrincipalOrgID` / `aws:ResourceOrgID` in resource policies or RCPs |

---

## 5. Workload identity on compute

| Compute | Mechanism | How creds reach the process |
|---|---|---|
| EC2 | Instance profile | IMDS `169.254.169.254`. **Enforce IMDSv2** (session token via PUT, hop limit 1, or 2 for containers). This blocks SSRF credential theft (the Capital One 2019 breach used IMDSv1 via SSRF). |
| Lambda | Execution role | Injected env vars; rotated by the service |
| ECS | Task role | Container credentials endpoint `169.254.170.2` |
| EKS – **IRSA** | Projected service-account JWT → `AssumeRoleWithWebIdentity` | Needs an OIDC provider per cluster and a trust policy per cluster/SA. Trust policies get big at scale. |
| EKS – **Pod Identity** | EKS Pod Identity Agent (DaemonSet) → EKS Auth API | Trust policy trusts `pods.eks.amazonaws.com` once. Associations are managed by EKS API. Supports session tags for ABAC. Preferred for new clusters. |

---

## 6. ABAC vs RBAC at scale

- **RBAC:** one role per team per environment. Simple, but role count explodes (teams × envs × accounts).
- **ABAC:** permissions keyed on tags. Example: allow `ec2:StopInstances` when `aws:ResourceTag/project == aws:PrincipalTag/project`.
  - Needs **tag governance**: Tag Policies plus an SCP that denies creating untagged resources and denies changing the `project` tag.
  - Works with IAM Identity Center attributes for access control (AD/Okta attributes become session tags).

---

## 7. Multi-account identity at enterprise scale
```
IdP (Okta / Entra ID) ──SCIM+SAML──► IAM Identity Center (in the management or a delegated admin account)
                                       │ Permission sets → provisioned as roles in each account
                                       ▼
                                  300 accounts (no IAM users anywhere)
```
- **No IAM users** for humans. Use break-glass users only, with hardware MFA, monitored by alarms.
- SCP guardrails at the root/OU level: deny leaving the org, deny disabling CloudTrail/GuardDuty/Config, region deny-list (with exemptions for global services), deny `iam:CreateUser`.
- **Least privilege at scale:** IAM Access Analyzer (unused access findings, policy generation from CloudTrail, external access findings, and **custom policy checks** in CI: "does this policy grant anything new versus the last version?").

---

## 8. Debugging `AccessDenied` systematically

1. **Read the error.** Most services now say *which* policy type denied: "…with an explicit deny in a service control policy" or "…because no identity-based policy allows…".
2. `aws sts get-caller-identity`. Are you who you think you are? (Wrong profile is the #1 cause.)
3. **CloudTrail** event: `errorCode`, `userIdentity.sessionContext`, the resource ARN actually requested.
4. **IAM Policy Simulator** / `aws iam simulate-principal-policy` (doesn't model SCPs/resource policies fully).
5. Check the chain: SCP → RCP → resource policy → identity → boundary → session policy → KMS key policy → VPC endpoint policy (often forgotten!) → S3 Block Public Access / Object Ownership.
6. Condition keys: is `aws:SourceVpce` or `aws:SourceIp` set? (`aws:SourceIp` is not present for calls through a VPC endpoint; use `aws:VpcSourceIp`.)

---

## 9. Senior interview questions — model answers

<details><summary><b>Q1. Can an SCP grant permissions?</b></summary>

No. SCPs (and RCPs, boundaries, session policies) only set the maximum. Effective permissions are the **intersection** of the limiting policies and the **union** of granting policies (identity + resource). SCPs also don't apply to the management account or to service-linked roles. That is why you should run no workloads in the management account.

</details>

<details><summary><b>Q2. Design: let 50 dev teams create their own IAM roles for Lambdas without becoming admins.</b></summary>

Permission boundaries. The platform team creates a boundary policy (e.g. no IAM, no Organizations, only resources tagged with the team). Dev roles get `iam:CreateRole` with a condition `iam:PermissionsBoundary == arn:…/team-boundary`. They also get denied `iam:DeleteRolePermissionsBoundary` and editing the boundary policy itself. Any role they create can never exceed the boundary. This prevents privilege escalation via `iam:PassRole` to a more powerful role. Also restrict `iam:PassRole` with `iam:PassedToService`.

</details>

<details><summary><b>Q3. How do you prevent data exfiltration from S3 even if creds leak?</b></summary>

"Data perimeter." Three controls:
1. **Identity perimeter:** RCP / bucket policy: deny unless `aws:PrincipalOrgID == o-xxxx` (allow AWS service principals via `aws:PrincipalIsAWSService`).
2. **Network perimeter:** deny unless `aws:SourceVpce` is in your endpoints, or `aws:SourceIp` is in corporate ranges (with `aws:ViaAWSService` exceptions).
3. **Resource perimeter:** VPC endpoint policy: allow only `aws:ResourceOrgID == o-xxxx`. This stops a compromised instance from copying data to an *attacker's* bucket.

Plus GuardDuty S3 protection, Macie for data classification, and CloudTrail data events on sensitive buckets.

</details>

<details><summary><b>Q4. Leaked access key found on GitHub. Runbook?</b></summary>

1) Deactivate (don't delete yet, keep it for forensics). 2) If it's a role session, revoke active sessions: the console adds a deny policy with `aws:TokenIssueTime` before now. 3) CloudTrail Lake / Athena: everything that key did (look for `CreateUser`, `CreateAccessKey`, `AttachRolePolicy`, `RunInstances` in odd regions, which usually means crypto-mining). 4) Check persistence: new users, keys, roles, Lambda functions, trust-policy edits. 5) Rotate, then delete. 6) Root cause plus prevention: secret scanning in CI, move to OIDC/roles, an SCP denying `iam:CreateAccessKey`. AWS also auto-applies the `AWSCompromisedKeyQuarantineV3` policy when it detects public exposure.

</details>

<details><summary><b>Q5. Why does my Lambda get AccessDenied on an SSE-KMS bucket even though the bucket policy allows it?</b></summary>

KMS. The Lambda role needs `kms:Decrypt` (and `kms:GenerateDataKey` for writes) on the key. The **key policy** must allow it too, directly or by delegating to IAM. If the key is in another account, both sides must allow. Also check `kms:ViaService` conditions and whether S3 Bucket Keys are enabled (that changes the encryption context to the bucket ARN).

</details>

---

## 10. Hands-on
```bash
aws sts get-caller-identity
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::<acct>:role/MyRole \
   --action-names s3:GetObject --resource-arns arn:aws:s3:::my-bucket/key
aws accessanalyzer validate-policy --policy-type IDENTITY_POLICY --policy-document file://p.json
# CI guardrail: fail the build if a new policy grants iam:PassRole on *
aws accessanalyzer check-access-not-granted --policy-document file://p.json \
   --access '[{"actions":["iam:PassRole"]}]' --policy-type IDENTITY_POLICY
```

## Further reading
- AWS docs: *Policy evaluation logic* (the flowchart) and *Cross-account policy evaluation logic*.
- AWS whitepaper: *Building a data perimeter on AWS*.
- Netflix: *Repokid* (automated least privilege) and *ConsoleMe* (credential brokering), open-sourced by Netflix security. See [03-architecture-deep-dives/04-netflix-delivery-chaos-and-security.md](../03-architecture-deep-dives/04-netflix-cloud-security.md).
