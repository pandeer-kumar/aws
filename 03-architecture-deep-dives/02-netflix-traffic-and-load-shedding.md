# Deep Dive 02 · Netflix Traffic Management: Zuul, Adaptive Concurrency, Prioritized Load Shedding

> **Interviewer lens:**
> - "Your service is overloaded. What do you drop first, and how does the system decide automatically?"
> - "Static rate limits vs adaptive concurrency limits: why did Netflix move to adaptive?"
> - "How does load shedding interact with autoscaling? Can shedding prevent scaling from kicking in?"
> - "Retry storms during an incident: how do you stop clients from making things worse?"
> - "Design an API gateway for 200M devices."

---

## 1. Zuul: the edge gateway

```
Devices ──► AWS ELB/NLB ──► Zuul cluster (per region) ──► origin services (API, playback, …)
                             • inbound filters: auth, device/geo detection, request priority
                             • endpoint: route/proxy (also cross-region during evacuation)
                             • outbound filters: headers, metrics
                             • Push: Pushy (WebSocket) to devices
```
- **Zuul 1** was blocking (servlet, one thread per connection). **Zuul 2** (2016) rebuilt it on **Netty**: asynchronous, non-blocking, event loops.
- Netflix's lesson from Zuul 2 (worth quoting in interviews): async gave big gains for **connection-heavy** workloads (persistent connections, push, WebSockets, slow clients). For CPU-bound proxying the gains were smaller than expected, and async made **debugging and context propagation much harder**. "Async is not free."
- **Pushy** is Netflix's WebSocket push service. It holds a huge number of persistent device connections at once (the 2024 *Pushy to the Limit* post has the current scale figures) so it can push events to clients instead of having them poll.

### AWS mapping
API Gateway (REST/HTTP/WebSocket APIs) or ALB + Envoy/NGINX on EKS/ECS. **CloudFront + Lambda@Edge / CloudFront Functions** for edge logic. For WebSockets at huge scale: NLB + your own Netty/Envoy fleet (API Gateway WebSocket has per-connection pricing and quotas).

---

## 2. Adaptive concurrency limits ("Performance Under Load", 2018)

**Problem with static limits** (e.g. "max 100 RPS per instance"): the correct limit changes with instance type, deploy version, request mix and downstream latency. A static number is always wrong. Too high → overload. Too low → wasted capacity.

**Idea:** treat each service like TCP congestion control. Use **Little's Law**:
```
concurrency (in-flight) = throughput × latency
```
If latency starts rising while throughput is flat, requests are **queuing**, so you're over capacity. The algorithm (Vegas / Gradient variants in Netflix's open-source `concurrency-limits` library):
```
gradient = minLatency (no-load RTT) / currentLatency
newLimit = currentLimit × gradient + queueSize
requests beyond the limit → rejected immediately (fast 429/503) instead of queued
```
Benefits: no manual tuning; servers protect themselves; clients get fast failures they can route around (retry on another instance, or degrade).

---

## 3. Prioritized load shedding at the gateway (2020)

**Insight:** not all requests are equal. A user pressing **Play** matters far more than a device **pre-fetching** artwork or sending logs.

Mechanism (as described in Netflix's 2020 post and re:Invent 2021 NFX302 talk):
1. Every request gets a **priority** in Zuul from attributes: request type (playback vs browse vs pre-fetch vs telemetry), device type, whether it's user-initiated, region.
2. Requests group into buckets: roughly **non-critical** (logs, background), **degraded experience** (pre-fetch, some personalisation), **critical** (playback, sign-in).
3. When a backend signals overload (or Zuul's own health/concurrency degrades), Zuul **progressively raises the priority threshold**. The lowest priorities are dropped first; the threshold climbs with load following a non-linear curve, so it sheds just enough.
4. **Devices are partners**: Netflix clients honour throttling (backoff, defer pre-fetch). Shedding a pre-fetch is invisible to users.
5. **Validated with chaos experiments**: inject load/latency, confirm that only low-priority availability drops while playback stays at ~100%.

Reported outcome: the system prevented a significant outage within days of rollout. Netflix's 2024 follow-up notes that gateway-level shedding couldn't distinguish priorities *inside* a single service.

---

## 4. Service-level prioritized load shedding (2024)

Moving shedding **into each service** (via a shared internal library):
- Priority classes: **CRITICAL, DEGRADED, BEST_EFFORT, BULK**.
- Example from Netflix: **PlayAPI** serves both user-initiated playback requests and pre-fetch (preparing streams you *might* play). Previously they were split into separate clusters, which costs more and creates more to manage. With in-service prioritization, one cluster serves both, and pre-fetch is shed first.
- **CPU-based shedding**: when CPU exceeds the autoscaling target, start shedding lowest priority progressively. Shedding starts *above* the autoscaling target, so **autoscaling still sees the load and scales out**. (A common mistake: shedding so early that utilisation never reaches the scaling threshold, so you never scale.)
- **Latency-based shedding** for IO-bound services (where CPU isn't the bottleneck). Tested on a content-origin service that feeds Open Connect.
- Results reported: under injected load, pre-fetch availability dropped while user-initiated availability stayed at 100%.
- Anti-thrash: when shedding, also signal clients to **not retry immediately**.

### Pseudocode you can whiteboard
```python
PRIORITY = {"CRITICAL": 0, "DEGRADED": 1, "BEST_EFFORT": 2, "BULK": 3}

def should_shed(req_priority, cpu, target=0.60, max_cpu=0.90):
    if cpu <= target:
        return False                       # let autoscaling see load first
    # fraction of the way from target to max (0..1)
    pressure = min(1.0, (cpu - target) / (max_cpu - target))
    # shed BULK first, then BEST_EFFORT, DEGRADED; CRITICAL only at the very end
    thresholds = {3: 0.0, 2: 0.25, 1: 0.6, 0: 0.95}
    if pressure < thresholds[PRIORITY[req_priority]]:
        return False
    # probabilistic within the band avoids cliffs
    return random.random() < pressure
```

---

## 5. Retry storms: how Netflix-style systems avoid them
- **Client retry budgets** and server-provided `Retry-After`/throttle signals.
- **Jittered** backoff across hundreds of millions of devices. Without jitter, a regional blip makes every device retry at the same second.
- **Hystrix** (now in maintenance) popularized circuit breakers + bulkheads + fallbacks. Netflix moved toward adaptive concurrency limits and platform-level resilience.
- During evacuation, Zuul **ramps** traffic rather than flipping 100% at once.

---

## 6. Designing this on AWS (interview template)
```
Clients ──► CloudFront (WAF rate rules, bot control)
         ──► ALB / NLB ──► Gateway fleet (Envoy/Zuul-like on EKS) 
                              • assigns priority header (x-priority) from route/device/user action
                              • adaptive concurrency filter (Envoy has adaptive_concurrency)
                              • global rate limits per API key (Envoy ratelimit + ElastiCache)
                       ──► Services with in-process shedding library
                              • reads x-priority; CPU/latency-based shedding
                              • returns 503 + Retry-After; emits metric shed_count{priority}
Autoscaling: target tracking on CPU 60%; shedding begins >60% → scaling still triggers.
Observability: per-priority availability SLOs (CRITICAL 99.99, BEST_EFFORT 99).
```

---

## 7. Interview drill

<details><summary><b>Q: "Why not just autoscale faster instead of shedding?"</b></summary>

Scaling takes minutes (launch + warm-up). Spikes and failovers happen in seconds. Scaling also depends on the control plane, which may be impaired during the very event causing the surge. Shedding is instant and data-plane-only. You need both: shedding for the seconds-to-minutes gap, autoscaling for sustained load. Design them so shedding doesn't hide the scaling signal.

</details>

<details><summary><b>Q: "How do you decide priorities?"</b></summary>

Map each request type to user-visible impact. Ask: "If this fails, does a user notice right now?" Playback and checkout are critical; personalisation is degraded mode (show generic rows); telemetry and pre-fetch are best-effort/bulk. Make the priority explicit in the request (header) and owned by the product team, and test it via chaos.

</details>

## Further reading
- Netflix Tech Blog: *Zuul 2: The Netflix Journey to Asynchronous, Non-Blocking Systems* (2016).
- Netflix Tech Blog: *Performance Under Load* (2018); GitHub `Netflix/concurrency-limits`.
- Netflix Tech Blog: *Keeping Netflix Reliable Using Prioritized Load Shedding* (Nov 2020); re:Invent 2021 NFX302.
- Netflix Tech Blog: *Enhancing Netflix Reliability with Service-Level Prioritized Load Shedding* (June 2024); InfoQ coverage Nov 2024; QCon SF 2025 talk.
- Netflix Tech Blog: *Pushy to the Limit: Evolving Netflix's WebSocket proxy for the future* (2024).
- Builders' Library: *Using load shedding to avoid overload*.

<!-- nav -->
---

← [Deep Dive 01 · Netflix: From One Data Center to Four-Region Active-Active on AWS](01-netflix-multi-region-active-active.md) · [🏠 Home](../README.md) · [Deep Dive 03 · Netflix Data Platform: EVCache, Data Abstraction Layers, Counters, Keystone](03-netflix-data-platform.md) →
