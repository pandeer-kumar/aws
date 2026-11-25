# Deep Dive 05 · Cell-Based Architecture & Shuffle Sharding (How AWS Limits Blast Radius)

> **Interviewer lens:**
> - "One tenant's poison request takes down your multi-tenant service for everyone. Redesign it."
> - "Explain shuffle sharding with numbers. Why is it better than plain sharding?"
> - "What goes in the cell router, and how do you keep it from becoming the single point of failure?"
> - "How do you migrate a customer between cells?"

---

## 1. Plain sharding vs cells vs shuffle sharding

Imagine 8 workers and many customers.

| Approach | A bad customer (poison pill / DDoS) affects |
|---|---|
| **No isolation** (every worker serves everyone) | All workers → **100% of customers** |
| **Sharding** into 4 shards of 2 workers | 1 shard → **25%** of customers |
| **Shuffle sharding**: each customer gets a random *pair* of 2 out of 8 workers | Only customers with the **exact same pair** are fully affected: C(8,2) = **28 combinations → ~3.6%** |

With 100 workers and 5 per customer: C(100,5) ≈ **75 million** combinations. Two customers sharing an identical shard is extremely rare. Partial overlap is common, but clients that **retry on another worker in their shard** still succeed as long as one of their 5 is healthy.

**Requirements for shuffle sharding to work:**
1. Clients (or the routing layer) must **retry across the shard's workers** (fault-tolerant client).
2. Failure must be **contained to the workers hit** (no shared downstream collapse).
3. Assignment must be stable and known (deterministic hash or stored mapping).

AWS uses this in **Route 53**: each hosted zone is assigned 4 name servers from a large pool (different TLDs: .com, .net, .org, .co.uk). A DDoS against one customer's domain doesn't take out other customers' DNS.

---

## 2. Cell-based architecture

```
                  ┌──────────────── Cell router (thin, simple, highly available) ───────────────┐
                  │  map: customerId → cellId   (stored in a replicated, cached table)            │
                  └───────┬──────────────────┬──────────────────┬──────────────────┬─────────────┘
                     ┌────▼────┐        ┌────▼────┐        ┌────▼────┐        ┌────▼────┐
                     │ Cell 1  │        │ Cell 2  │        │ Cell 3  │  ...   │ Cell N  │
                     │ LB      │        │         │        │         │        │         │
                     │ compute │        │  same   │        │  same   │        │  same   │
                     │ DB      │        │         │        │         │        │         │
                     └─────────┘        └─────────┘        └─────────┘        └─────────┘
       Each cell: full independent stack, own data store, own quotas, ideally own AWS account.
       Each cell spans 3 AZs (cells are a logical partition; AZs are a physical one).
```

### Why cells
- **Blast radius = 1/N** for bugs, poison pills, bad deploys (deploy one cell first!), and capacity overload.
- **Known maximum size.** You load-test a cell to its max once. Scaling = adding cells, not growing a cell into unknown territory. Each cell stays inside tested limits.
- **Faster recovery**: smaller data stores, smaller caches to warm.

### Designing the router
- **Keep it dumb**: lookup + forward. No business logic, rarely deployed.
- The mapping table is small, read-mostly and heavily cached, and the router keeps serving from its cache if the mapping store is down (static stability).
- Options: DNS-based (each cell has a hostname; the client gets its cell's endpoint once at login), an API Gateway/Lambda/Envoy lookup layer, or a token claim (`cell=7` in the JWT).

### Cell sizing
Too few cells → big blast radius. Too many → operational overhead (more deployments, more dashboards) and lower utilisation. Common choice: cells sized so the largest customer fits in one, with spare capacity. Very large customers get dedicated cells.

### Migrating a customer between cells
1. Copy data to the target cell (snapshot + change stream).
2. Brief write freeze or dual-write with a version check.
3. Flip the router mapping (atomic update).
4. Drain the old cell. Keep the data for rollback for a period.

### Cell-based on AWS
- **Cell = AWS account** (via Organizations/Control Tower Account Factory): separate quotas, IAM boundaries and billing.
- Deploy pipeline: cell 1 (canary cell) → wave of cells → … (see the IaC chapter).
- AWS whitepaper: *Reducing the Scope of Impact with Cell-Based Architecture* (Well-Architected guidance).

---

## 3. Related isolation patterns
| Pattern | Idea |
|---|---|
| **Bulkheads** | Separate thread/connection pools per dependency, so one slow dependency can't exhaust all threads |
| **Per-tenant queues / fair queuing** | One tenant's backlog doesn't delay others (SQS fair queues) |
| **Rate limits per tenant** | Token bucket per API key (API Gateway usage plans) |
| **Zonal isolation** ("AZ-independence") | Requests stay within one AZ end to end, so an AZ failure is clean and zonal shift works |

---

## 4. Interview drill

<details><summary><b>Q: "Redesign our multi-tenant SaaS: one tenant's huge export job took down everyone."</b></summary>
Short term: per-tenant rate limits and concurrency caps for heavy APIs; move exports to an async job queue with per-tenant fairness; bulkhead export workers from the interactive path. Medium term: shuffle-shard tenants over worker pools, so a poison tenant only affects a small set of others, with client-side retry across their shard. Long term: cell-based architecture (cell = account), router by tenant ID, largest tenants in dedicated cells, deploy cell-by-cell. Explain the blast-radius maths for each step.
</details>
