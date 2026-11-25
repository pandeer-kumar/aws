# 03 · VPC & Networking: How Packets Actually Move in AWS

> **Interviewer lens.** Networking is where "10 years on AWS" claims get tested hardest:
> - "There is no broadcast and no ARP spoofing in a VPC. Why? What is actually implementing my 'subnet'?"
> - "Security Group vs NACL: stateful vs stateless. Show me a case where the NACL breaks return traffic."
> - "Design connectivity for 200 VPCs, 3 regions, and an on-prem DC. TGW? Cloud WAN? PrivateLink? Why?"
> - "My NAT Gateway bill is $40k/month. Diagnose and fix."
> - "Explain the DNS resolution path from an EC2 instance to an on-prem name and back."
> - "Overlapping CIDRs between two acquired companies. How do you connect them?"

---

## 1. What a VPC really is

A VPC is **not** a network of switches. It is a **software-defined overlay** implemented by the **Nitro card** in every host and by AWS's VPC **mapping service**, which AWS has described in re:Invent networking deep dives.

```
 Instance A (10.0.1.10)                         Instance B (10.0.2.20)
   │ ENI                                           ▲ ENI
   ▼                                               │
 Nitro card on host 1  ── encapsulates ──►  physical AWS network ──► Nitro card on host 2
   • looks up "who owns 10.0.2.20 in VPC-123?" in the mapping service
   • enforces Security Groups (stateful, per ENI) and NACLs
   • rewrites/encapsulates; the physical network never sees your 10.x addresses
```
Consequences you'll be asked about:
- **No broadcast/multicast** (except Transit Gateway multicast). ARP is answered by the hypervisor, so ARP spoofing is impossible.
- **Source/destination check.** The Nitro card drops packets whose src/dst isn't the ENI's own IP. Disable it for NAT instances or appliances.
- **Reserved IPs:** first 4 + last 1 of every subnet (`.0` network, `.1` VPC router, `.2` DNS, `.3` reserved, `.255` broadcast). A `/28` gives 11 usable addresses, not 16.
- The **VPC router** at `.1` is virtual and has no throughput limit to plan for. Throughput limits come from instance network bandwidth, **packets-per-second (PPS)**, and **connection tracking** limits.

### Connection tracking: the hidden limit
Security groups are **stateful**, so the Nitro card tracks every flow. Each instance type has a conntrack allowance. When it's exhausted, new connections are silently dropped. Check `conntrack_allowance_exceeded` in the ENA driver stats (`ethtool -S eth0`).
- Flows are **untracked** (and therefore unlimited) if the SG allows all traffic in *both* directions (`0.0.0.0/0` all protocols, inbound and outbound). Some high-PPS UDP workloads use this deliberately.
- Idle tracked TCP connections time out after **5 days** by default; this is configurable per ENI. NLB's idle timeout is 350s. Mismatched timeouts cause the classic "random connection resets after idle" problem.

---

## 2. Subnets, routing, gateways

| Component | Facts that matter |
|---|---|
| **Subnet** | Lives in exactly **one AZ**. "Public" just means its route table has `0.0.0.0/0 → igw-…`. |
| **Route table** | Longest-prefix match. The `local` route (VPC CIDR) can't be removed but **can** be pointed more specifically at an appliance (middlebox insertion). |
| **Internet Gateway** | Horizontally scaled, no bandwidth limit, does 1:1 NAT between the private IP and the public/Elastic IP. |
| **NAT Gateway** | Zonal. Up to 100 Gbps, 55,000 simultaneous connections **per unique destination** per IP (add up to 8 secondary IPs). **$0.045/GB processed** in many regions, which is where bills explode. AWS has also introduced a **regional** NAT Gateway mode that spans AZs. Check whether it's available in your region and how it handles AZ failure before you rely on it. |
| **Egress-only IGW** | IPv6 equivalent of NAT (outbound only). IPv6 is all globally unique, so there's no NAT. |
| **Elastic IP** | Static public IPv4. Since Feb 2024 **every** public IPv4 costs about $0.005/hr. Audit them. |

### Security Group vs NACL

| | Security Group | Network ACL |
|---|---|---|
| Level | ENI | Subnet |
| State | **Stateful** (return traffic auto-allowed) | **Stateless** (must allow return ports explicitly) |
| Rules | Allow only | Allow + Deny, numbered, first match wins |
| References | Can reference other SGs (`sg-app` allows from `sg-alb`) | CIDRs only |
| Use | Primary micro-segmentation | Coarse "block this /16" or compliance guardrail |

**Classic NACL bug:** you allow inbound 443 and outbound 443. The client's reply goes back to its **ephemeral port** (1024–65535). Without an outbound rule for those ports, responses are dropped. Fix: allow outbound ephemeral ports.

---

## 3. Connecting VPCs: choosing the right tool

| Option | Topology | Transitive? | Overlapping CIDR? | Cost model | Use when |
|---|---|---|---|---|---|
| **VPC Peering** | 1:1 mesh | ❌ | ❌ | Data transfer only (free intra-AZ) | A handful of VPCs, max throughput, lowest cost |
| **Transit Gateway** | Hub-and-spoke, regional; peer TGWs across regions | ✅ via route tables | ❌ (needs NAT) | Per attachment-hour + $0.02/GB | Tens to thousands of VPCs + VPN/DX, segmentation via TGW route tables |
| **Cloud WAN** | Global, policy-defined (JSON network policy, segments) | ✅ | ❌ | Core network edge + attachments | Multi-region global network managed as code |
| **PrivateLink (Interface endpoint / endpoint service)** | Consumer → provider service via NLB/GWLB | N/A (service-level) | ✅ **works with overlapping CIDRs** | Per endpoint-hour/AZ + per GB | SaaS, shared services, M&A with overlapping IPs, least-privilege exposure of *one* service |
| **VPC Lattice** | Service network (L7, app-level) | ✅ | ✅ | Per service-hour + requests + GB | Service-to-service across accounts with IAM auth policies, no routing work |
| **Site-to-Site VPN** | IPsec, 2 tunnels × ~1.25 Gbps each | via TGW | — | Hourly + data | Quick hybrid, backup for DX. ECMP over TGW for more bandwidth. |
| **Direct Connect** | Dedicated 1/10/100/400 Gbps fibre | via DX Gateway + TGW | — | Port-hour + lower DTO | Predictable hybrid bandwidth/latency |

### Reference: multi-account hub-and-spoke (Landing Zone network)
```
               ┌───────────── Network account ─────────────┐
 On-prem ─DX──►│ DX Gateway ─► Transit Gateway (per region) │◄── TGW peering ──► other regions
  (VPN backup) │      │      route tables: prod / nonprod / shared / inspection
               │  Inspection VPC (AWS Network Firewall or GWLB+3rd-party, appliance mode ON)
               │  Egress VPC (centralized NAT GW per AZ)
               │  Shared-services VPC (Route 53 Resolver endpoints, AD, CI runners)
               └──────────────┬───────────────────────────────┘
                      RAM-shared TGW / subnets
        ┌────────────────────┼────────────────────┐
     Prod VPCs          Non-prod VPCs        Sandbox VPCs (isolated route table)
```
Key details interviewers probe:
- **Appliance mode** on the TGW attachment for the inspection VPC. Without it, the forward and return flows can hit firewalls in different AZs, and the stateful firewall drops asymmetric traffic.
- **Centralized egress** saves NAT GW count, but you pay TGW data processing ($0.02/GB) **plus** NAT ($0.045/GB). For heavy S3/DynamoDB traffic, *always* use **gateway endpoints** (free) in each spoke instead.
- IPAM (VPC IP Address Manager) to allocate non-overlapping CIDRs from pools per region/OU.

---

## 4. VPC endpoints

| Type | Services | How it works | Cost |
|---|---|---|---|
| **Gateway endpoint** | S3, DynamoDB only | A prefix-list route in your route table | **Free** |
| **Interface endpoint** | ~all services, your own PrivateLink services | ENIs with private IPs in your subnets; private DNS overrides the public name | ~$0.01/hr per AZ + $0.01/GB |
| **Gateway Load Balancer endpoint** | Your inspection appliances | Route-table target that GENEVE-encapsulates to GWLB | per hour + GB |

Endpoint **policies** are a data-perimeter control (see IAM chapter §9 Q3).

---

## 5. DNS: Route 53 Resolver and hybrid DNS

```
EC2 ──query──► VPC+2 resolver (Route 53 Resolver, "AmazonProvidedDNS")
                 ├── Private Hosted Zone associated with VPC?           → answer
                 ├── Resolver forwarding rule (corp.example.com)?       → Outbound endpoint ENIs ──► on-prem DNS
                 └── else public recursion
On-prem DNS ──conditional forward (aws.example.internal)──► Inbound endpoint ENIs ──► Resolver
```
- Each ENI may make **1024 packets/sec** to the Resolver. DNS-heavy apps (Java without DNS caching, or chatty service discovery) hit this. Symptom: intermittent `UnknownHostException`. Fix: local caching (systemd-resolved, nscd, node-local DNS on EKS), and tune JVM `networkaddress.cache.ttl`.
- **Route 53 Resolver DNS Firewall** blocks exfiltration domains.
- Share forwarding rules org-wide with **RAM**.

---

## 6. Load balancers (networking view)

| | ALB | NLB | GWLB |
|---|---|---|---|
| Layer | 7 (HTTP/1.1, HTTP/2, gRPC, WebSocket) | 4 (TCP/UDP/TLS), QUIC passthrough | 3 (GENEVE to appliances) |
| Source IP | Lost, use `X-Forwarded-For` | Preserved (for instance/IP targets) | Preserved |
| Static IP | No (use Global Accelerator) | Yes, one per AZ / EIP | — |
| Scaling | Scales by adding nodes; extreme spikes may need pre-warm via "LCU reservation" | Near-instant, millions of flows | |
| Cross-zone | On by default (can be disabled per target group) | Off by default (costs inter-AZ data if on) | |
| Security groups | Yes | Yes (since 2023) | No |

Deep dive on load-balancer behaviour: [04-compute-and-load-balancing.md](./04-compute-and-load-balancing.md).

---

## 7. Data transfer costs (the networking line nobody reads until it's huge)

| Path | Typical price |
|---|---|
| Inbound from internet | Free |
| Same AZ, private IP | Free |
| **Cross-AZ** | $0.01/GB **each direction** ($0.02 round trip) |
| Cross-region | $0.02/GB (varies) |
| Internet egress | ~$0.09/GB first tiers (cheaper via CloudFront) |
| NAT GW processing | +$0.045/GB |
| TGW processing | +$0.02/GB |

**Kafka/Cassandra/Elasticsearch replication across 3 AZs is often the #1 hidden cost.** Use rack/AZ-aware consumers (e.g. Kafka `client.rack` with follower fetching) to read from the same AZ.

---

## 8. Senior interview questions — model answers

<details><summary><b>Q1. NAT Gateway costs $40k/month. Fix it.</b></summary>

1) **VPC Flow Logs** (or NAT GW CloudWatch `BytesOutToDestination`) and Athena to find the top destinations. 2) Usually it's S3/DynamoDB/ECR/CloudWatch Logs traffic going via NAT. Add a **gateway endpoint** for S3/DynamoDB (free) and interface endpoints for ECR (`api`, `dkr`), Logs and STS. Interface endpoints at $0.01/GB vs NAT's $0.045/GB still win at volume. 3) Check cross-AZ NAT usage (subnets routing to a NAT in another AZ pay cross-AZ *and* NAT). 4) Move container image pulls to a pull-through cache in ECR. 5) For big third-party egress, consider IPv6 + egress-only IGW (no processing fee).

</details>

<details><summary><b>Q2. Connect two VPCs with overlapping 10.0.0.0/16.</b></summary>

Options: (a) **PrivateLink**. Expose only the needed services via NLB + endpoint service; overlapping CIDRs don't matter because the consumer gets ENIs in its own space. (b) **VPC Lattice** for service-level connectivity. (c) **Private NAT Gateway**: add a secondary non-overlapping CIDR (e.g. 100.64.0.0/16) to each VPC, put a private NAT GW there, and route via TGW. (d) Re-IP long-term with IPAM. A senior answer chooses by the shape of the traffic: a few services → PrivateLink/Lattice; broad network access → private NAT, then a re-IP plan.

</details>

<details><summary><b>Q3. Intermittent connection timeouts from Lambda in a VPC to RDS at peak.</b></summary>

Suspects, in order: (1) **DB connection exhaustion**. Thousands of concurrent Lambdas each open connections. Fix with RDS Proxy (pooling and multiplexing). (2) Security-group or subnet IP exhaustion (Hyperplane ENIs are shared per subnet+SG combo since 2019, so this is less common). (3) Too few NAT GW ports per destination (if calling external APIs). (4) DNS throttling on the resolver. Evidence: RDS `DatabaseConnections`, Proxy `ClientConnections` vs `DatabaseConnectionsCurrentlyBorrowed`, NAT `ErrorPortAllocation`.

</details>

<details><summary><b>Q4. Why use Network Firewall in a separate inspection VPC with appliance mode?</b></summary>

Centralized, consistent policy for east-west and egress traffic with a single management point. Appliance mode keeps both directions of a flow on the same AZ's firewall endpoint (TGW otherwise picks the AZ per direction, which breaks stateful inspection). The trade-off is extra hops, TGW processing cost and a shared blast radius, so you scale it per region and monitor its health closely.

</details>

---

## 9. Hands-on
```bash
# Who's talking through my NAT? (Flow Logs → S3 → Athena)
SELECT dstaddr, sum(bytes)/1e9 AS gb FROM vpc_flow_logs
WHERE interface_id = 'eni-<natgw-eni>' AND action='ACCEPT'
GROUP BY dstaddr ORDER BY gb DESC LIMIT 20;

# Reachability Analyzer: "why can't A reach B?" (analyses SG/NACL/routes without sending packets)
aws ec2 create-network-insights-path --source i-aaa --destination i-bbb --protocol tcp --destination-port 5432
```

## Further reading
- re:Invent "Another day, another billion flows" (VPC internals, Hyperplane) and "A day in the life of a billion packets".
- AWS whitepaper: *Building a Scalable and Secure Multi-VPC AWS Network Infrastructure*.
- AWS docs: *Amazon EC2 instance network bandwidth* (PPS, conntrack allowances, ENA Express).

<!-- nav -->
---

← [02 · IAM Deep Dive: How AWS Decides "Allow" or "Deny"](02-iam-deep-dive.md) · [🏠 Home](../README.md) · [04 · Compute & Load Balancing: EC2/Nitro, Auto Scaling, Containers, Lambda](04-compute-and-load-balancing.md) →
