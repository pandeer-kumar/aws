# 03 · Security & Governance at Organization Scale

> **Interviewer lens.**
> - "Design a landing zone for 300 accounts. What OUs, which SCPs, where do logs go?"
> - "Explain KMS envelope encryption. Why can't KMS encrypt my 1 GB file directly?"
> - "GuardDuty fired `CryptoCurrency:EC2/BitcoinTool`. Incident response, step by step."
> - "How do you rotate a database password used by 40 services with zero downtime?"
> - "What's in the shared responsibility model for Lambda vs EC2?"

---

## 1. Multi-account landing zone

```
Root (management account: billing, Organizations, nothing else)
├── Security OU
│   ├── Log Archive  (org CloudTrail, Config, VPC flow logs; Object Lock; tight access)
│   └── Audit / Security Tooling (delegated admin: GuardDuty, Security Hub, Inspector, Macie, Detective, Access Analyzer)
├── Infrastructure OU
│   ├── Network (TGW, DX, Inspection, Egress, Route 53 Resolver)
│   └── Shared Services (CI/CD, ECR, AMI factory, Identity Center delegated admin)
├── Workloads OU
│   ├── Prod OU   → one account per app/team per env
│   └── NonProd OU
├── Sandbox OU (budget limits, auto-nuke)
├── Suspended OU (deny-all SCP for closing accounts)
└── Policy Staging OU (test new SCPs here first!)
```
Built with **Control Tower** (account factory, guardrails as preventive SCPs/RCPs + detective Config rules + proactive CFN hooks) or custom (Org Formation, Terraform AFT, LZA, the Landing Zone Accelerator).

**Why many accounts?** The account is the strongest isolation boundary: blast radius, per-account service quotas, clean billing, simpler IAM.

### Baseline SCPs (examples)
- Deny leaving the organization; deny disabling CloudTrail/Config/GuardDuty/Security Hub.
- Region deny-list except approved regions (exempt global services: IAM, Organizations, Route 53, CloudFront, STS, Support, and Bedrock global inference with `aws:RequestedRegion = "unspecified"` if you use it).
- Deny root user actions in member accounts (**centralized root access** now lets you remove member root credentials entirely).
- Deny creating IAM users/access keys (except break-glass path).
- Deny `ec2:RunInstances` unless IMDSv2 required (`ec2:MetadataHttpTokens = required`).
- Deny unencrypted EBS/RDS/S3 settings; protect security roles from modification.

**Gotcha:** an SCP can block AWS's own automation if written carelessly. Test in the Policy Staging OU, and use `aws:PrincipalArn` exemptions for platform roles.

---

## 2. KMS: envelope encryption

```
Encrypt:
 App ──GenerateDataKey(KeyId)──► KMS (HSM-backed key, never leaves KMS)
     ◄── plaintext data key + encrypted data key (ciphertext blob)
 App encrypts data locally with plaintext data key (AES-256-GCM), discards plaintext key,
     stores encrypted data key next to the data.
Decrypt:
 App ──Decrypt(encrypted data key)──► KMS (checks key policy + IAM + grants + encryption context)
     ◄── plaintext data key → decrypt data locally
```
- **Why:** KMS `Encrypt` handles at most **4 KB**, network round trips are slow, and KMS has request quotas. Envelope encryption means one KMS call per data key, not per byte.
- **Encryption context:** non-secret key/value pairs bound to the ciphertext (AAD). Must match on decrypt. Logged in CloudTrail, so it's good for audit and least privilege (`kms:EncryptionContext:tenant`).
- **Key types:** AWS-owned (free, invisible), AWS-managed (`aws/s3`, can't change policy, **can't share cross-account**), **customer-managed** (your policy, rotation, cross-account grants). Use customer-managed keys for anything cross-account or with compliance needs.
- **Rotation:** automatic yearly (configurable 90 days–7 years) keeps old backing keys, so old ciphertext still decrypts. **Multi-Region keys** share key material across regions (for DR, global tables, client-side encryption).
- **Deleting a key** = crypto-shredding all data under it. Mandatory 7–30 day waiting period. Alarm on `ScheduleKeyDeletion`.
- **Quotas:** shared request quota per account per region for cryptographic operations. S3 Bucket Keys and data key caching (AWS Encryption SDK) prevent throttling.

---

## 3. Secrets

| | Secrets Manager | SSM Parameter Store |
|---|---|---|
| Rotation | Built-in (Lambda rotation; managed rotation for RDS) | DIY |
| Cost | $0.40/secret/month + API | Standard tier free |
| Cross-account | Resource policy | Advanced tier sharing (RAM) |

**Zero-downtime rotation (alternating users):** Secrets Manager's "alternating users" strategy keeps two DB users. Rotation updates the *inactive* user's password and flips `AWSCURRENT`. Clients that cache the secret keep working until their cache refreshes. Clients must re-fetch the secret on auth failure. Better still, use **IAM database authentication** or RDS-managed master passwords and avoid long-lived DB passwords entirely.

---

## 4. Threat detection & response

| Service | Detects |
|---|---|
| **GuardDuty** | Threat intel + ML on CloudTrail, VPC Flow, DNS logs, EKS audit/runtime, S3 data events, RDS logins, Lambda network, malware scans (EBS/S3). Extended Threat Detection correlates multi-stage attack sequences. |
| **Inspector** | CVEs in EC2, ECR images, Lambda; network reachability |
| **Macie** | PII/sensitive data in S3 |
| **Detective** | Investigation graph across entities |
| **Security Hub** | Aggregation, CSPM standards (CIS, FSBP), cross-region/account, findings in OCSF/ASFF format |
| **Security Lake** | Normalized (OCSF) security data lake in S3 |

### IR runbook: `CryptoCurrency:EC2/BitcoinTool.B!DNS`
1. **Contain:** replace the instance SG with an isolation SG (no ingress/egress; existing tracked connections may persist, so also apply a NACL deny if needed). Detach from ASG/ELB (prevent replacement confusion; set ASG standby). Do **not** terminate.
2. **Preserve:** EBS snapshots, memory capture (SSM + tooling), tag `Quarantine=true`.
3. **Investigate:** how did it get in? CloudTrail (`RunInstances` by whom? or an exploited app?), Inspector CVEs, IMDS credential use from *outside* AWS (GuardDuty `InstanceCredentialExfiltration`). Revoke the instance role sessions (`aws:TokenIssueTime` deny).
4. **Eradicate & recover:** rebuild from a known-good AMI, patch, rotate secrets.
5. **Lessons:** automate steps 1–2 with EventBridge → Step Functions/SSM Automation.

---

## 5. Shared responsibility nuances
| | EC2 | ECS on Fargate | Lambda | RDS | S3 |
|---|---|---|---|---|---|
| OS patching | **You** | AWS | AWS | AWS (you schedule maintenance) | — |
| Runtime/language patching | You | You (image) | AWS (managed runtimes), you for container images | — | — |
| Network config (SG) | You | You | You (if VPC) | You | Bucket policy |
| Data encryption & access | You | You | You | You | You |

---

## 6. Perimeter & app protection
- **WAF** on CloudFront/ALB/API GW/AppSync: managed rule groups (Core, Known Bad Inputs, SQLi), rate-based rules, Bot Control, ATP/ACFP (account takeover/creation fraud).
- **Shield Standard** (free, L3/4) / **Shield Advanced** (DDoS response team, cost protection, health-based detection).
- **Firewall Manager** to enforce WAF/SG/Network Firewall policies org-wide.
- **Verified Access** for zero-trust access to internal apps (no VPN).

---

## 7. Senior interview questions — model answers

<details><summary><b>Q1. A developer needs prod read access during an incident. How?</b></summary>
Just-in-time elevated access: an Identity Center permission set (e.g. `ProdReadOnly`) granted temporarily via **Temporary elevated access management (TEAM)** or an approval workflow, time-boxed, with session tags and CloudTrail `SourceIdentity`. Auto-expire. No shared accounts. Review afterwards.
</details>

<details><summary><b>Q2. How do you prove to an auditor that all S3 buckets are encrypted and private?</b></summary>
Preventive: account-level Block Public Access via SCP (deny `s3:PutAccountPublicAccessBlock` changes), default encryption, RCPs restricting principals to the org. Detective: Config rules (`s3-bucket-public-read-prohibited`, `s3-bucket-server-side-encryption-enabled`) in an org conformance pack with an aggregator, plus Security Hub FSBP controls. Evidence: **Audit Manager** assessment reports. Show the control is enforced *and* continuously evaluated.
</details>
