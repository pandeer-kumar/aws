# GenAI 02 · RAG on AWS: Bedrock Knowledge Bases and Building Your Own

> **Interviewer lens:**
> - "Design RAG over 20M internal documents with per-user permissions."
> - "Your RAG answers are wrong 15% of the time. How do you find out *why*?"
> - "Chunking strategies: fixed, semantic, hierarchical. When each?"
> - "OpenSearch Serverless vs Aurora pgvector vs S3 Vectors as the vector store?"
> - "How do you keep the index fresh and delete data for GDPR requests?"

---

## 1. The RAG pipeline

```
INGEST (offline / incremental)
  Sources (S3, Confluence, SharePoint, Salesforce, web crawler, custom connectors)
    → parse (text, tables, images; Bedrock Data Automation or a foundation model as parser for complex PDFs)
    → chunk (fixed / hierarchical / semantic / custom Lambda)
    → embed (Titan Text Embeddings v2, Cohere Embed, multimodal embeddings)
    → vector store (+ metadata: source, ACL groups, dates, doc type)

QUERY (online)
  user question → (optional) query rewrite / decomposition
    → embed → hybrid search (vector + keyword/BM25) with METADATA FILTERS (ACL, date)
    → rerank (Cohere Rerank / Amazon Rerank) top-50 → top-5
    → prompt = instructions + chunks (with citations) + question
    → LLM → guardrail contextual-grounding check → answer + citations
```

### Bedrock Knowledge Bases gives you
- Managed ingestion jobs (incremental sync; only changed documents re-embedded), parsing, chunking strategies, embeddings.
- Vector store choices: **OpenSearch Serverless**, OpenSearch managed clusters, **Aurora PostgreSQL (pgvector)**, **S3 Vectors** (lowest cost, for large and less latency-sensitive corpora), Neptune Analytics (**GraphRAG**), Pinecone, Redis, MongoDB Atlas.
- `Retrieve` (chunks only) and `RetrieveAndGenerate` (end-to-end with citations, session context).
- **Metadata filtering** (and implicit filters extracted from the query by a model), **reranking**, **query decomposition**.
- **Structured data retrieval**: natural-language-to-SQL over Redshift / Glue Data Catalog. Use it when the answer is in tables, not prose.
- **GraphRAG** with Neptune Analytics: entities and relations are extracted into a graph; retrieval does vector search, then expands across the graph to connected content. Helps multi-hop questions ("which projects did the team that built X also own?").

### When to build your own instead
Custom chunking/parsing per doc type, very specific ranking, a multi-index fan-out, strict latency budgets, or existing search infrastructure. Typical build: Step Functions/Lambda ingest → Bedrock embeddings → OpenSearch (hybrid search) → your app. Use `Retrieve` from KB with your own prompt for a middle ground.

---

## 2. Chunking

| Strategy | Good for | Risk |
|---|---|---|
| Fixed size (e.g. 300–500 tokens, 10–20% overlap) | Homogeneous prose | Splits tables/sections mid-thought |
| **Hierarchical** (small child chunks for matching, return the bigger parent) | Long technical docs | More tokens per answer |
| Semantic (split where embedding similarity drops) | Mixed docs | Ingestion cost (uses a model) |
| Structure-aware (by heading, by table, by code function) | Manuals, code, FAQs | Needs a custom parser (Lambda transform) |
| No chunking (one chunk per doc) | Short FAQs/tickets | Big docs blow the context window |

Rule: **chunk by meaning, retrieve small, give the model enough context.** Always store source + section + URL metadata for citations.

---

## 3. Vector store choice

| | OpenSearch Serverless | Aurora PostgreSQL pgvector | S3 Vectors | OpenSearch managed |
|---|---|---|---|---|
| Hybrid (BM25 + vector) | ✅ | Via full-text search + SQL | Vector only (+ filters) | ✅ |
| Cost profile | Minimum OCUs (baseline cost even when idle) | Instance-based, can share an existing DB | **Pay per storage + query**, very cheap at rest | Instance-based |
| Latency | ms | ms | sub-second (higher than in-memory) | ms |
| Scale | Large | Millions–100M vectors with tuning (HNSW) | Billions | Large |
| Best when | Default for Knowledge Bases, hybrid needed | Data already in Postgres, transactional joins with ACL tables | Huge corpora, cost first, tolerant latency; tier cold vectors | Need full control/tuning |

---

## 4. Permissions-aware RAG (the enterprise question)

Never rely on the LLM to "not mention" documents a user can't see. **Filter before retrieval.**
1. At ingest, attach ACL metadata per chunk: `allowed_groups: ["eng", "hr-managers"]` (from the source system's ACLs; refresh on change).
2. At query, resolve the user's groups (IdP/Identity Center), then a `Retrieve` **metadata filter** `allowed_groups IN user.groups`.
3. For document-level security from enterprise sources, consider **Amazon Q Business** (which handles ACL ingestion for its connectors), or Kendra GenAI Index as the retriever.
4. Audit: log which chunk IDs were returned to which user.

### GDPR delete / freshness
- Source deletion → incremental sync removes those vectors. For "delete user X now", delete the source doc, trigger sync, and verify with a query. Document the SLA (sync interval).
- Keep `doc_id` in metadata so you can delete by ID.
- Freshness: event-driven sync (S3 events → `StartIngestionJob`, with rate limits on job concurrency).

---

## 5. Debugging bad answers: separate retrieval from generation

```
For a labelled eval set (question, expected answer, expected source docs):
 1. Retrieval quality:  recall@k / MRR. Was the right chunk in the top-k at all?
      ✗ → fix chunking, embeddings, hybrid search, metadata, query rewriting, reranker
 2. Generation quality given correct context: faithfulness/groundedness, correctness, completeness
      ✗ → fix prompt, model choice, context ordering, citation instructions
 3. Guardrail contextual grounding threshold: are you blocking good answers or letting bad ones through?
```
Tools: **Bedrock Evaluations** (LLM-as-a-judge for model and RAG evaluation: correctness, completeness, faithfulness, citation precision/coverage), RAGAS, and your own golden sets. **Run evals in CI** for every prompt/model/chunking change, like unit tests.

Common root causes: table content lost in PDF parsing; acronyms not matching (needs hybrid/keyword); too many chunks diluting the context; stale index; questions that need aggregation (use structured retrieval/SQL, not RAG).

---

## 6. Senior interview question — model answer

<details><summary><b>Design RAG over 20M documents with per-user permissions, p95 < 3s, 2k QPS peak.</b></summary>

- **Ingest:** connectors → S3 landing → Step Functions: parse (FM parser for complex PDFs, Textract for scans) → structure-aware chunking → batch embeddings (Bedrock **batch** for the initial backfill to halve cost) → OpenSearch (hybrid) with ACL + tenant metadata. Incremental updates via change events. About 20M docs × ~10 chunks = 200M vectors. Size the index (HNSW memory: 200M × 1024 dims × 4 bytes ≈ 800 GB raw), or use binary/byte quantization, or S3 Vectors for the cold tail with OpenSearch for the hot set.
- **Query:** an API layer resolves user groups (cached) → query rewrite (small, fast model) → hybrid search k=50 with ACL filter → rerank to 5 → generation on a mid-size model with prompt caching of the system prompt → stream. Cache answers for frequent identical questions (semantic cache keyed by embedding + ACL group set).
- **Scale/latency:** 2k QPS → CRIS profile + quota increases + token-bucket limits per team; budget: retrieval 300ms, rerank 150ms, TTFT under 1s, streaming.
- **Quality & safety:** golden eval sets per domain in CI; contextual grounding guardrail; citations mandatory; feedback thumbs → eval set growth.
- **Ops:** dashboards for retrieval latency, empty-result rate, guardrail intervention rate, cost per query.

</details>
