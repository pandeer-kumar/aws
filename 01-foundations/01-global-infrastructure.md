# 01 · Global Infrastructure: Regions, AZs, Edge, and the Control/Data Plane Split

> **Interviewer lens (10-yr AWS hire).** Expect questions like these:
> - "What *physically* is an AZ, and why does AWS tell you to use three, not two?"
> - "Explain control plane vs data plane. Which one should your recovery plan depend on?"
> - "What is static stability? Give an example of an architecture that is *not* statically stable."
> - "Why is `us-east-1` special, and which 'global' services actually live there?"
> - "AZ `us-east-1a` in my account vs yours. Same building?"
>
> By the end of this chapter you should be able to answer every one of these from first principles.

---

## 1. The physical hierarchy

```
Partition (aws, aws-cn, aws-us-gov, aws-eusc …)   ← hard isolation: separate IAM, separate accounts
 └── Region (eu-west-1, us-east-1 …)              ← independent deployment of (almost) every service
      ├── Availability Zone (AZ)                   ← 1+ discrete data centers, own power/cooling/network
      │    └── Data center(s)
      ├── Local Zones / Wavelength                 ← extensions of a region closer to users
      └── Regional services (S3, DynamoDB, SQS, Lambda …) span all AZs
Edge: 600+ CloudFront PoPs, Route 53 anycast DNS, Global Accelerator anycast IPs
```

| Concept | What it really is | Why it matters to you |
|---|---|---|
| **Partition** | A totally separate copy of AWS. ARNs start `arn:aws:`, `arn:aws-cn:`, `arn:aws-us-gov:` | IAM roles/policies cannot cross partitions. Your multi-partition story is basically "two clouds". |
| **Region** | A geographic area with ≥3 AZs, connected by redundant private fibre | The **blast-radius boundary** for nearly all AWS services. AWS deploys to regions one at a time, in waves. |
| **AZ** | One or more data centers, kilometres apart (AWS says "within 100 km / 60 miles" of each other), on separate flood plains and power substations | The **fault-isolation boundary you design around day-to-day**. Inter-AZ latency is single-digit ms. |
| **AZ ID** (`use1-az1`) | The *physical* AZ | AZ **names** (`us-east-1a`) are shuffled per account. To line up with another account (e.g. a PrivateLink provider), compare **AZ IDs**. |
| **Local Zone** | A small AWS footprint in a metro area, parented to a region | Single-digit-ms latency to end users (gaming, media editing). Fewer services, and no AZ redundancy inside it. |
| **Outposts** | AWS racks in *your* data center, managed by AWS | The control plane is still in the parent region. If the link drops, running workloads continue but you cannot change them. |

### Why three AZs and not two?
1. **Quorum.** Many systems (etcd, ZooKeeper, Aurora's 4-of-6 write quorum) need a majority. With 2 AZs, losing one leaves no majority.
2. **Capacity math.** To survive losing one AZ at 100% load:
   - 2 AZs → each must run at ≤50% (100% over-provisioned).
   - 3 AZs → each must run at ≤66% (50% over-provisioned).
   - N AZs → headroom = `1/(N-1)`.
3. **Grey failures.** With 3 AZs you can *compare* AZs. One AZ having 5× the error rate of the other two is a clear signal. With 2 you can't tell which side is sick.

---

## 2. Control plane vs data plane (the most important idea in this guide)

Every AWS service has two halves:

| | Control plane | Data plane |
|---|---|---|
| Does what | Create / modify / delete resources | Does the actual work of the running resource |
| Examples | `RunInstances`, `CreateTable`, `UpdateFunctionConfiguration`, Route 53 `ChangeResourceRecordSets`, IAM `CreateRole` | EC2 instance running, `GetItem`/`PutItem`, Lambda `Invoke`, Route 53 answering DNS queries, STS validating a signed request |
| Complexity | High (workflows, databases, orchestration) | Kept deliberately simple |
| Availability | Lower (and sometimes **only in one region**) | Higher. Designed to keep running even if the control plane is down |

**Rule:** *Your recovery path must depend only on data planes.*

Bad DR plan: "When AZ-a fails, the ASG will launch new instances in AZ-b and AZ-c."
→ That depends on the EC2 control plane (`RunInstances`). That is exactly what degrades during big events. In the Oct-2025 us-east-1 event, EC2 launches failed for hours after DynamoDB itself recovered.

Better: **pre-provision** capacity in all AZs (static stability) and have health checks shift traffic away. That uses only the ELB/Route 53 data planes.

### Global services and where their control planes live

| Service | Data plane | Control plane |
|---|---|---|
| IAM | Globally replicated (authN/authZ happens in every region) | `us-east-1` |
| Route 53 | 4 anycast name-server stripes, globally distributed | `us-east-1` |
| CloudFront | 600+ PoPs | `us-east-1` |
| ACM certs for CloudFront | — | Must be created in `us-east-1` |
| Organizations / SCP edits | Enforced everywhere | `us-east-1` |

→ **Interview answer:** "During a us-east-1 control-plane event I may not be able to *change* Route 53 records or IAM policies. So my failover must not require changing them. I use **Route 53 health checks** (data plane), or **Application Recovery Controller (ARC) routing controls**, whose cluster is spread across 5 regions and is built to be flipped during an event."

---

## 3. Static stability

> A system is **statically stable** if it keeps working correctly when a dependency fails, *without needing to change anything*.

Examples:

| Statically stable | Not statically stable |
|---|---|
| 3 AZs, each at ≤66% load. Lose one AZ, the ELB stops routing there, and the others absorb the traffic | 3 AZs at 90%. Lose one AZ and you need the ASG to launch more (control plane) |
| Lambda reads config cached in memory and keeps working if the config service dies | Each request calls the config service, so it fails when that service fails |
| EC2 instances keep their IAM role creds (cached, auto-refreshed well before expiry) | App calls STS `AssumeRole` per request |
| NAT gateway per AZ | One NAT gateway in AZ-a shared by all AZs. Lose AZ-a and every private subnet loses egress |
| RDS Multi-AZ with standby already running | "We'll restore from snapshot if the primary dies" |

AWS designs its own services this way. For example, an EC2 instance's network configuration is pushed to the hypervisor and keeps working if the EC2 control plane is down.

---

## 4. Cell-based architecture (how AWS isolates *itself*)

AWS splits big services into **cells**: complete, independent copies of the service, each serving a subset of customers. A thin **cell router** maps customer → cell.

```
            ┌───────── cell router (simple, static mapping) ─────────┐
            │                                                         │
     ┌──────▼─────┐   ┌────────────┐   ┌────────────┐          ┌──────▼─────┐
     │  Cell 1    │   │  Cell 2    │   │  Cell 3    │   ...    │  Cell N    │
     │ full stack │   │ full stack │   │ full stack │          │ full stack │
     └────────────┘   └────────────┘   └────────────┘          └────────────┘
```

- A poison-pill request or bad deploy hurts **1/N** of customers, not all of them.
- Deployments go **one cell → one AZ → one region → waves**. See [02-operating-aws/02-iac-and-deployments.md](../02-operating-aws/02-iac-and-deployments.md).
- **Shuffle sharding** goes further. Each customer gets a random *combination* of workers, so two customers rarely share all their workers. Covered in depth in [03-architecture-deep-dives/05-cells-and-shuffle-sharding.md](../03-architecture-deep-dives/05-cells-and-shuffle-sharding.md).

---

## 5. Edge network

| Service | What it is | When |
|---|---|---|
| **CloudFront** | CDN + TLS termination + L7 (Functions/Lambda@Edge) at PoPs | Static/dynamic content, API acceleration, WAF at the edge, origin shielding |
| **Global Accelerator** | 2 static anycast IPs. TCP/UDP enters AWS backbone at nearest PoP | Non-HTTP, gaming/VoIP, need static IPs, very fast regional failover (no DNS TTL) |
| **Route 53** | Authoritative DNS (100% SLA) with routing policies | Latency/geo/weighted/failover routing |

**CloudFront vs Global Accelerator in one sentence:** CloudFront *caches and terminates* HTTP at the edge. Global Accelerator *proxies* TCP/UDP over the AWS backbone to regional endpoints and fails over in seconds, without DNS TTL problems.

---

## 6. Why `us-east-1` keeps appearing in outage headlines
- It is the oldest, largest region. It has the most customers and the most internal dependencies.
- Several global control planes live there (see table above).
- Many companies run *only* there, so even partial impact is very visible.
- Lesson: don't put your **only** copy of anything critical there by default. Make sure your failover does not need us-east-1 control planes. See the outage case studies in [03-architecture-deep-dives/06-aws-outage-postmortems.md](../03-architecture-deep-dives/06-aws-outage-postmortems.md).

---

## 7. Senior interview questions — model answers

<details><summary><b>Q1. Your app runs in 2 AZs behind an ALB. Is it highly available?</b></summary>

"Partially. Two problems. (1) **Capacity:** if each AZ runs at more than 50%, losing one overloads the other and causes a cascading failure. (2) **Dependencies:** what about the DB, NAT gateway and caches? Often one of them is single-AZ. I'd move to 3 AZs and keep each below about 66% at peak. I'd add a NAT gateway per AZ and run RDS Multi-AZ or Aurora with readers in other AZs. I'd use **zonal autoshift** or ARC **zonal shift** so I can pull an AZ out of the load balancer during grey failures. I'd also confirm cross-zone load balancing settings match my capacity plan."

</details>

<details><summary><b>Q2. What is a grey failure and how do you detect one?</b></summary>

"A partial failure where health checks still pass but customers see errors or latency. Examples: packet loss on one rack, a slow EBS volume, one AZ's DNS resolver degraded. Detection: per-AZ metrics (ALB `HTTPCode_Target_5XX_Count` and `TargetResponseTime` **by AZ**), outlier detection comparing AZs, deep health checks (but not *too* deep, see Q3), and CloudWatch Internet Monitor / canaries from outside. Mitigation: zonal shift away from the bad AZ. You can do this because you are statically stable with N-1 capacity."

</details>

<details><summary><b>Q3. Shallow vs deep health checks?</b></summary>

"A shallow check (`/ping` returns 200) proves the process is alive. A deep check also tests dependencies (DB, cache). Deep checks catch more, but they're dangerous. If the shared DB blips, *every* host fails its health check at once, and the LB has nothing healthy left. Pattern: shallow checks for the LB. Deep checks feed alarms and dashboards, not routing. ALB and NLB also **fail open**: if all targets are unhealthy, they route to all of them, which is a deliberate safety net. Route 53 health checks behave similarly when every record in a set is unhealthy."

</details>

<details><summary><b>Q4. Explain how you'd fail over a region during a us-east-1 control-plane outage.</b></summary>

"Pre-decided, pre-provisioned, data-plane only. Standby region already scaled (or warm with a scaling headroom plan that doesn't rely on the failed region). Data already replicated (Aurora Global Database, DynamoDB global tables, S3 CRR). Traffic shift with ARC routing controls or Route 53 health-check-driven failover records, which are data plane. No `ChangeResourceRecordSets` needed in the moment. Practice it with game days. Netflix evacuates regions routinely, which is why it works when they need it."

</details>

<details><summary><b>Q5. A partner says 'deploy in us-east-1a to be in our AZ'. What's wrong?</b></summary>

"AZ names are mapped per account. Their `us-east-1a` may be my `us-east-1c`. Compare **AZ IDs** (`use1-az4`) from `aws ec2 describe-availability-zones`."

</details>

---

## 8. Hands-on (30 min)
```bash
# AZ name → AZ ID mapping for YOUR account
aws ec2 describe-availability-zones --region us-east-1 \
  --query 'AvailabilityZones[].[ZoneName,ZoneId,ZoneType]' --output table

# Which regions are enabled (opt-in regions are off by default)
aws account list-regions --region-opt-status-contains ENABLED ENABLED_BY_DEFAULT
```

## Further reading
- AWS whitepaper: *AWS Fault Isolation Boundaries* (control vs data plane, static stability, global services). This is the single best primer.
- Builders' Library: *Static stability using Availability Zones*.
- Builders' Library: *Workload isolation using shuffle-sharding*.
- AWS whitepaper: *Reducing the Scope of Impact with Cell-Based Architecture*.

<!-- nav -->
---

[🏠 Home](../README.md) · [02 · IAM Deep Dive: How AWS Decides "Allow" or "Deny"](02-iam-deep-dive.md) →
