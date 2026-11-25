# Deep Dive 06 · Learning from AWS's Own Outages (Post-Event Summaries)

> **Interviewer lens:**
> - "Walk me through a major AWS outage you studied. What was the root cause *chain*, and what did you change in your own architecture because of it?"
> - "Why did recovery take hours when the trigger was fixed in minutes?"
> - "What is a 'latent' bug, and how do you find one before it finds you?"
> - "How do you design so an us-east-1 event doesn't take you down?"

AWS publishes **Post-Event Summaries (PES)** at `aws.amazon.com/premiumsupport/technology/pes/`. Reading them is the single best way to learn how hyperscale systems fail. Below are four of the most instructive, with the engineering lessons.

> Timelines and mechanisms are summarized from AWS's PES pages and reputable coverage (InfoQ, The Register, ThousandEyes). Read the originals for exact times.

---

## 1. October 19–20, 2025 · us-east-1 · DynamoDB DNS → EC2 → NLB (≈ 15 hours of impact)

### Root cause chain
```
DynamoDB DNS management automation
  ├─ DNS Planner: watches load balancer health/capacity, produces numbered "plans" (which LBs back the endpoint)
  └─ DNS Enactor (one per AZ, independent, for resilience): applies plans to Route 53
1. Enactor #1 picks up an OLD plan but is unusually slow (retries on several endpoints).
2. Meanwhile the Planner produces newer plans; Enactor #2 applies a NEW plan everywhere,
   then runs CLEANUP, which deletes plans much older than the one it just applied.
3. Enactor #1 (delayed) finally overwrites the regional endpoint with its OLD plan.
   Its staleness check had passed long before, because the check ran at the start.
4. Cleanup deletes that old plan → regional record dynamodb.us-east-1.amazonaws.com is now EMPTY.
5. The system is in an inconsistent state that blocks further automated updates → needs a manual fix.
```
### Cascade
- Everything resolving the DynamoDB regional endpoint fails, **including AWS internal services**.
- **EC2: DropletWorkflow Manager (DWFM)** keeps leases on physical hosts ("droplets") and depends on DynamoDB. Lease checks failed. When DynamoDB came back, DWFM had to re-establish a huge number of leases at once. Work timed out and was retried → **congestive collapse** (a metastable failure), which needed throttling and manual restarts.
- **Network Manager** then had a large backlog of network-state propagation → new instances launched **without network connectivity** for a while.
- **NLB** health checks hit those new instances, failed, removed capacity, re-added it when checks passed → **flapping** → NLB connection errors. Lambda, ECS, STS, Redshift, etc. were also affected.

### AWS remediations (as stated)
- Disabled the DynamoDB DNS Planner/Enactor automation **worldwide** until the race is fixed and safeguards are added.
- **Velocity control for NLB**: limit how much capacity health-check failures can remove at once.
- Improve DWFM recovery (throttling, test suites for recovery at scale).

### Lessons for *your* systems
1. **Check-then-act races in automation.** Re-validate freshness *at write time* (compare-and-swap with a version/generation number), not only at start.
2. **Cleanup jobs are dangerous.** Never delete the thing currently in use. Add "is this referenced?" guards.
3. **Automation needs rate limits / velocity controls** (cap the % of capacity it can change per minute).
4. **Health checks that remove capacity must fail open / be rate-limited.** Otherwise a systemic problem turns into a capacity collapse.
5. **Recovery is a workload too.** Systems must handle "everyone reconnects at once" (backlogs, lease storms) with built-in throttling.
6. **Regional services have regional failure modes.** Multi-AZ isn't multi-region. Tier-0 needs a multi-region plan whose failover doesn't depend on us-east-1 control planes.

---

## 2. December 7, 2021 · us-east-1 · Internal network congestion (≈ 7 hours)

- **Trigger:** an automated activity to scale capacity of a service hosted in AWS's **main network** caused unexpected behaviour from a large number of clients inside AWS's **internal network** (where many control planes and internal services run). A **surge of connection attempts** overwhelmed the networking devices between the two networks.
- **Amplification:** clients retried (a latent bug: backoff didn't behave as intended in this case) → more congestion. **Internal monitoring was impaired**, which slowed diagnosis. DNS within the internal network was moved to relieve load.
- **Impact:** customer data planes (running EC2 instances, etc.) mostly kept working, but **control planes and APIs** suffered: console, STS-dependent features, API Gateway, EventBridge, Connect, ECS/Fargate launches. Customers could not *change* things, which is why control-plane-dependent recovery plans failed.
- **AWS fixes:** disabled the scaling activity until fixed, fixed the client backoff bug, more network capacity, isolation of the Service Health Dashboard / support contact paths from the affected network.
- **Lessons:** retries without good backoff amplify congestion; your monitoring must not share fate with what it monitors; **data plane good, control plane bad** is a real mode, so don't build recovery on control planes.

---

## 3. November 25, 2020 · us-east-1 · Kinesis front-end thread exhaustion (≈ 17 hours for full recovery)

- **Trigger:** routine capacity addition to the Kinesis **front-end fleet**. Each front-end server keeps a thread for **every other front-end server** (to learn the shard map). Adding servers pushed every server past the **OS thread limit** → servers couldn't build the shard-map cache → requests failed.
- **Why recovery was slow:** servers had to be restarted gradually (a few hundred per hour), because each restarting server competes for resources to build the shard map. Bringing the whole fleet up at once would fail again.
- **Cascade:** Cognito, CloudWatch (metrics ingestion uses Kinesis), Lambda (metrics), EventBridge, and the **Service Health Dashboard updates** (they depended on Cognito).
- **AWS fixes:** move to larger servers (fewer servers = fewer threads), thread-limit alarms, **cellularization** of the front-end, move CloudWatch to a separate, partitioned front-end fleet.
- **Lessons:** O(N²) or O(N)-per-node designs hit hidden limits as N grows; scale tests should include *fleet size*, not just traffic; **shared dependency fan-in** (many services on one stream service) widens the blast radius; your status page must not depend on the platform it reports on.

---

## 4. February 28, 2017 · us-east-1 · S3 "the typo" (≈ 4 hours)

- **Trigger:** an engineer debugging the S3 billing system ran an established playbook command to remove a small number of servers. An input was entered incorrectly, and a **much larger set** was removed, including servers for the **index subsystem** (metadata/location of all objects) and the **placement subsystem** (allocating storage for new objects).
- **Why it took hours:** both subsystems needed a **full restart**, which hadn't been done in large regions for years. The system had grown, and restart + safety checks took far longer than expected.
- **Cascade:** everything using S3 in us-east-1, including the AWS Service Health Dashboard (which couldn't show the outage because its assets were in S3).
- **AWS fixes:** the tool now removes capacity **slowly** and **refuses** to go below minimum capacity; faster recovery by partitioning the index subsystem into smaller cells; dashboard moved to multi-region.
- **Lessons:** operational tools need guardrails (rate limits, minimum-capacity checks, confirmation for large changes); **regularly exercise cold restarts** (recovery paths you never run don't work); cells reduce recovery time.

---

## 5. Also worth reading
- **April 2011 EBS (us-east-1):** a network change shifted traffic onto a lower-capacity network → EBS nodes lost replicas → a **re-mirroring storm** consumed all capacity → stuck volumes. Netflix largely survived, which strengthened its push for AZ-independent design and Chaos Monkey.
- **Dec 24, 2012 ELB (us-east-1):** a maintenance process accidentally deleted ELB state data → many ELBs misconfigured → Netflix streaming impacted on Christmas Eve → Netflix's multi-region active-active program.

---

## 6. Patterns across all of these (say this in interviews)

| Pattern | Seen in | Your defence |
|---|---|---|
| Automation/tooling with too much power | 2017 S3, 2021, 2025 | Velocity limits, min-capacity guards, staged rollout for config and automation |
| Latent bug revealed by scale or rare timing | 2020 Kinesis, 2025 race | Scale tests on fleet size; formal methods/TLA+ for concurrency (AWS uses TLA+); chaos |
| Recovery slower than failure | All | Practise cold starts; throttle reconnection; cells shrink recovery |
| Retry/reconnect storms (metastable) | 2021, 2025 DWFM | Backoff + jitter + retry budgets; admission control |
| Monitoring/status shares fate | 2017, 2020, 2021 | Out-of-band monitoring; status page in another region/provider |
| Control plane down, data plane OK | 2021, 2025 | Static stability; data-plane-only failover (ARC, health checks) |
| us-east-1 concentration | All | Tier-0 multi-region; avoid us-east-1-only dependencies in failover |

---

## 7. Interview drill

<details><summary><b>Q: "What did you change in your architecture after the Oct 2025 outage?"</b></summary>
(Model answer for a senior.) "We audited our hard dependencies on us-east-1. We found our CI/CD, our secrets fetch at container start, and an IdP integration all had only-us-east-1 paths. We (1) made workloads cache secrets and config so running tasks survive a dependency outage, (2) moved tier-0 to an active/standby second region with ARC routing controls so failover needs no Route 53 API changes, (3) added velocity limits to our own automation that removes capacity on failed health checks, and (4) added a game day simulating 'DynamoDB endpoint unresolvable' with FIS plus DNS fault injection. We measured RTO and found the next bottleneck: cold start of the standby's connection pools."
</details>

## Sources
- AWS PES index: https://aws.amazon.com/premiumsupport/technology/pes/
- Oct 2025: https://aws.amazon.com/message/101925/ · InfoQ: https://www.infoq.com/news/2025/11/aws-dynamodb-outage-postmortem · The Register: https://www.theregister.com/2025/10/23/amazon_outage_postmortem/ · ThousandEyes: https://www.thousandeyes.com/blog/aws-outage-analysis-october-20-2025
- Dec 2021: https://aws.amazon.com/message/12721/
- Nov 2020: https://aws.amazon.com/message/11201/
- Feb 2017: https://aws.amazon.com/message/41926/
- Paper: *Metastable Failures in Distributed Systems* (Bronson et al., HotOS 2021) and *Metastable Failures in the Wild* (OSDI 2022).
- Paper: *How Amazon Web Services Uses Formal Methods* (CACM 2015).
