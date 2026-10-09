# 🧠 OmniAnalyst   — The Intelligent Document & Query Intelligence Platform
### Enhanced Multi-Agent Architecture with GraphRAG + LangExtract + Anti-Chunking + OCR + Full Middleware Stack

---

## 🔭 What Is This?

OmniAnalyst   is a **production-grade, multi-agent AI platform** with two operating modes:

| Mode | Trigger | Example |
|------|---------|---------|
| **Simple Query Mode** | User types a plain question, no file | *"What are the top revenue metrics for SaaS?"* |
| **Document Intelligence Mode** | User uploads a file (any format) | Upload a scanned PDF, Excel sheet, image, DOCX |

In both modes, a full **swarm of specialist agents** — governed by a 13-layer middleware stack, a 4-layer memory system, and a Time Travel checkpoint engine — produces rich, streamed, grounded responses. When files are uploaded, the system solves the two hardest problems in document AI: **chunking accuracy** and **retrieval precision**, via a GraphRAG + LangExtract + Semantic Chunking pipeline before the agents even start reasoning.

---

## 🌊 The Two Entry Points

```
                        ┌──────────────────────────┐
                        │       USER INTERFACE      │
                        └────────────┬─────────────┘
                                     │
              ┌──────────────────────▼──────────────────────┐
              │           QUERY INTENT CLASSIFIER           │
              │  (LLM-powered — runs before anything else)  │
              └────────────────┬───────────────────────────┘
                               │
              ┌────────────────▼───────────────────┐
              │                                    │
     ┌────────▼─────────┐              ┌──────────▼──────────┐
     │  SIMPLE QUERY    │              │  DOCUMENT + QUERY   │
     │  MODE            │              │  MODE               │
     │                  │              │                     │
     │  Direct agent    │              │  Full Ingestion     │
     │  reasoning path  │              │  → GraphRAG         │
     │  (no file)       │              │  → LangExtract      │
     │                  │              │  → Agent Swarm      │
     └──────────────────┘              └─────────────────────┘
```

### Simple Query Mode Flow
```
User: "Summarize the latest GDPR obligations for SaaS companies"
        │
        ▼
Query Intent Classifier → SIMPLE_QUERY
        │
        ▼
TodoListMiddleware → task decomposition
        │
        ▼
LLMToolSelectorMiddleware → picks: [web_search_tool, memory_retrieval_tool]
        │
        ▼
Orchestrator → routes to: Summary Agent + Audit Agent
        │
        ▼
Episodic Memory → "user previously asked about GDPR in session 47"
        │
        ▼
Streamed response → user
```

### Document Intelligence Mode Flow
```
User uploads: scanned_invoice.pdf + "Extract all line items and totals"
        │
        ▼
Query Intent Classifier → DOCUMENT_QUERY
        │
        ▼
Ingestion Pipeline (OCR → Parse → Clean → Anti-Chunking Layer)
        │
        ▼
LangExtract → Structured Entity Extraction with grounding
        │
        ▼
NetworkX entity graph construction with LangExtract-grounded relations
        │
        ▼
Hybrid Retrieval: Vector Search + Graph Traversal + Metadata Filtering
        │
        ▼
Orchestrator routes to Specialist Agent Swarm
        │
        ▼
Streamed, grounded, structured response → user
```

---

## 📥 Ingestion Pipeline (The Anti-Chunking Engine)

This is the most critical differentiator of OmniAnalyst  . Instead of naively chunking documents, the system runs a **5-stage intelligent ingestion pipeline** that preserves meaning, structure, and relationships before any agent ever sees the content.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INTELLIGENT INGESTION PIPELINE                   │
│                                                                     │
│  Stage 1: FILE TYPE DETECTION + ROUTING                             │
│  ─────────────────────────────────────                              │
│   .pdf (digital)  → PDFPlumber / PyMuPDF                           │
│   .pdf (scanned)  → OCR Engine (see below)                         │
│   .xlsx / .csv    → Pandas → schema profiling                       │
│   .docx           → python-docx → section/table extraction         │
│   .png / .jpg     → Vision OCR (multimodal LLM + Tesseract)        │
│   .txt / .md      → Direct injection                               │
│   .pptx           → python-pptx → slide text + speaker notes       │
│                                                                     │
│  Stage 2: OCR LAYER (for non-digital sources)                       │
│  ─────────────────────────────────────────                          │
│   • Tesseract (fast, open-source OCR baseline)                     │
│   • Surya OCR (layout-aware, handles tables + rotated text)         │
│   • Vision LLM (GPT-4o Vision / Claude Vision for complex layouts) │
│   • Page-level confidence scoring → auto-fallback between engines  │
│   • Deskew + denoise preprocessing (OpenCV) before OCR             │
│   • Output: clean Unicode text with bounding box metadata           │
│                                                                     │
│  Stage 3: STRUCTURAL PRESERVATION                                   │
│  ────────────────────────────────                                   │
│   • Document hierarchy recovered: H1 → H2 → H3 → paragraph         │
│   • Tables extracted as structured JSON, not flattened text         │
│   • Figures/charts → Vision LLM description + alt-text             │
│   • Each element tagged: {type, page, section, bbox, source_uri}   │
│                                                                     │
│  Stage 4: ANTI-CHUNKING LAYER (Solve the chunking problem)          │
│  ─────────────────────────────────────────────────────             │
│   Strategy selected dynamically by ChunkingStrategySelector Agent: │
│                                                                     │
│   ┌─ Semantic Chunking (default for prose docs)                    │
│   │   Groups sentences by embedding similarity — no mid-idea cuts  │
│   │   Up to ~70% accuracy improvement vs naive fixed-size chunking  │
│   │                                                                  │
│   ├─ Structural/Hierarchical Chunking (for reports, manuals)        │
│   │   Chunks follow H1/H2/H3 boundaries — never crosses sections   │
│   │                                                                  │
│   ├─ Parent-Document Retrieval (for dense technical PDFs)           │
│   │   Large parent chunks stored; small child chunks retrieved;     │
│   │   context always from parent → no truncated answers            │
│   │                                                                  │
│   ├─ Late Chunking (for long-context models)                        │
│   │   Embed THEN chunk — preserves cross-sentence meaning           │
│   │                                                                  │
│   └─ Token-Based (for tight context windows / cost mode)            │
│       Exact token counts, preserves sentence boundaries             │
│                                                                     │
│   Each chunk tagged with rich metadata:                             │
│   {chunk_id, parent_id, doc_id, page, section, heading,            │
│    entity_mentions[], keywords[], created_at, confidence}          │
│                                                                     │
│  Stage 5: PII SCAN                                                  │
│  ─────────────                                                      │
│   PIIMiddleware scans ALL extracted text before anything else       │
│   Redacts: SSNs, emails, phone numbers, credit card numbers,        │
│   medical IDs, passport numbers → replaced with [REDACTED_TYPE]    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🔬 LangExtract Layer — Structured Entity Extraction

After ingestion, **LangExtract** (Google's open-source library) runs over the cleaned text to extract semantically grounded, schema-consistent structured data.

### Why LangExtract (not just LLM prompting)?

| Problem with raw LLM extraction | How LangExtract solves it |
|----------------------------------|--------------------------|
| Schema inconsistency across runs | Enforces a fixed output schema via few-shot examples + Controlled Generation |
| No source traceability | Maps every entity back to its **exact character offset** in the source |
| Fails on long documents | Optimized chunking + parallel processing + multi-pass for high recall |
| Needs fine-tuning per domain | Adapts to any domain with just a few examples, no fine-tuning |
| LLM hallucinates entity values | Grounded extraction — outputs tied to source spans |

### What Gets Extracted

```python
# Example: Invoice uploaded
ExtractionSchema = {
    "vendor": {"type": "string", "grounded": True},
    "invoice_date": {"type": "date", "grounded": True},
    "line_items": [{
        "description": "string",
        "quantity": "number",
        "unit_price": "number",
        "total": "number"
    }],
    "total_amount": {"type": "currency", "grounded": True},
    "payment_terms": {"type": "string", "grounded": True}
}

# Output: structured JSON + character offset of every field in source PDF
# Visual HTML report showing highlights in original document
```

### LangExtract Output → Agent Input
The structured extraction output flows directly into the Orchestrator as **grounded context**, eliminating hallucination at the retrieval stage.

---

## 🕸️ Knowledge Graph Layer (GraphRAG)

After LangExtract, the system builds a **Knowledge Graph** of all entities, relationships, and metadata from uploaded documents using LangChain's `LangExtract-grounded graph extractor` and stores it in NetworkX graph artifacts.

### Why Knowledge Graph for Metadata?

Standard vector-only retrieval fails at:
- Multi-hop questions (*"What did the CFO say about the topic the CEO mentioned in Q3?"*)
- Relationship-aware questions (*"Which vendors appear in both the contract and the invoice?"*)
- Cross-document linking (*"Find all entities mentioned in both the PDF and the Excel"*)

<br>

### Graph Construction Flow

```
Clean Text (from LangExtract)
        │
        ▼
LangExtract-grounded graph extractor
  ├── Entity Extraction: Person, Org, Product, Date, Amount, Location
  ├── Relationship Extraction: OWNS, REPORTS_TO, MENTIONS, CONTAINS, SIGNED_BY
  └── Confidence scoring per extracted triple
        │
        ▼
NetworkX graph artifact store
  ├── Nodes: entities with properties + source_uri + page_number + chunk_id
  ├── Edges: typed relationships with confidence scores
  └── Full-text index on entity.id for fuzzy search
        │
        ▼
Metadata Graph built alongside vector store
```

### Hybrid Retrieval (Vector + Graph + Metadata)

```
User Query: "What are all obligations related to ABC Corp in the contract?"
        │
        ▼
┌──────────────────────────────────────────────────┐
│            HYBRID RETRIEVAL ENGINE               │
│                                                  │
│  ① Vector Search                                 │
│     Semantic similarity → top-k chunks           │
│                                                  │
│  ② Graph Traversal (from vector results)         │
│     "ABC Corp" entity → graph hop → all related  │
│     nodes (obligations, signatories, dates)       │
│                                                  │
│  ③ Metadata Self-Querying                        │
│     LLM infers metadata filters automatically:   │
│     {doc_type: "contract", party: "ABC Corp"}     │
│     Applies filter BEFORE vector search           │
│                                                  │
│  ④ BM25 Keyword Search                           │
│     Exact keyword match on full-text index        │
│     Fused with vector scores via RRF              │
│                                                  │
│  ⑤ Parent-Document Expansion                    │
│     Retrieved child chunks → fetch parent chunks │
│     → full context window, no truncation          │
│                                                  │
│  ⑥ ColBERT + FAISS Late-Interaction Retrieval              │
│     All retrieved candidates reranked by         │
│     relevance to query before LLM ingestion       │
└──────────────────────────────────────────────────┘
        │
        ▼
Top-K grounded, reranked context → Agent State
```

---

## 🤖 Multi-Agent Swarm (Full)

### Orchestrator (Supervisor)

```
                    ┌──────────────────────────┐
                    │     ORCHESTRATOR AGENT    │
                    │  LangGraph StateGraph     │
                    │                          │
                    │  Receives:               │
                    │  - User query            │
                    │  - Extracted entities    │
                    │  - Knowledge graph ctx   │
                    │  - Memory snapshots      │
                    │                          │
                    │  Decides:               │
                    │  - Which agents to call  │
                    │  - In what order         │
                    │  - In parallel or series │
                    └──────────┬───────────────┘
                               │
    ┌──────────────────────────┼──────────────────────────────┐
    │          │               │              │               │
    ▼          ▼               ▼              ▼               ▼
Stats      Visual           SQL           Summary          Audit
Agent      Agent            Agent         Agent            Agent
    │          │               │              │               │
    ▼          ▼               ▼              ▼               ▼
Compare    Ingestion        Research      Extraction       Compliance
Agent      Agent            Agent         Agent            Agent
```

### All Agents

| Agent | Role | Key Tools |
|-------|------|-----------|
| **Orchestrator** | Supervisor, routes tasks | LangGraph StateGraph, swarm coordinator |
| **Ingestion Agent** | File parsing, OCR, structure recovery | ShellToolMiddleware, FilesystemMiddleware |
| **Extraction Agent** | LangExtract schema extraction | LangExtract, python-docx, pdfplumber |
| **Graph Builder Agent** | Knowledge graph construction | NetworkX graph artifacts, LangExtract-grounded relations |
| **Stats Agent** | Statistical analysis, anomaly detection | Pandas, scipy, DuckDB |
| **Visual Agent** | Chart generation, data visualization | matplotlib, plotly |
| **SQL Agent** | Natural language to SQL | DuckDB, SQLAlchemy |
| **Summary Agent** | Executive summaries, TLDR | SummarizationMiddleware |
| **Compare Agent** | Multi-file semantic diffing | Embedding diff, graph comparison |
| **Audit Agent** | PII scan, compliance risk flagging | PIIMiddleware, regex rules |
| **Research Agent** | Web search for simple queries | Tavily / SerpAPI |
| **Chunking Strategy Agent** | Dynamically picks chunking strategy per doc | LangChain text splitters |
| **Reflection Agent** | Reviews other agent outputs for quality | Critique loop with Summary Agent |

---

## 🔌 Full Middleware Stack

All 13 middleware layers from v1, now enhanced with new additions:

```
Incoming Request
      │
      ▼
[1]  PIIMiddleware              ← Before model: redact sensitive data in query + docs
      │
      ▼
[2]  SummarizationMiddleware    ← Before model: auto-compress history at token limits
      │
      ▼
[3]  ContextEditingMiddleware   ← modify_model_request: inject graph context, metadata
      │
      ▼
[4]  LLMToolSelectorMiddleware  ← modify_model_request: pick tools per agent per turn
      │
      ▼
[5]  TodoListMiddleware         ← Before model: decompose complex queries into tasks
      │
      ▼
[6]  ModelCallLimitMiddleware   ← Before model: cap API calls per session
      │
      ▼
[7]  ToolCallLimitMiddleware    ← wrap_tool_call: cap tool executions
      │
      ▼
[8]  ToolRetryMiddleware        ← wrap_tool_call: retry failed tools
      │
      ▼
[9]  LLMToolEmulator            ← wrap_tool_call: simulate tools in test/sandbox mode
      │
      ▼
[10] ModelFallbackMiddleware    ← wrap_model_call: GPT-4o → Claude → Gemini fallback
      │
      ▼
[11] HumanInTheLoopMiddleware   ← After model: pause before destructive/risky actions
      │
      ▼
[12] ShellToolMiddleware        ← Tool registry: persistent shell for file ops
      │
      ▼
[13] FilesystemFileSearch       ← Tool registry: search uploaded file directories
      │
      ▼
[14] SemanticCacheMiddleware    ← wrap_model_call: Redis semantic cache (NEW)
      │
      ▼
[15] RetrievalGroundingMiddleware ← After model: verify response is grounded (NEW)
      │
      ▼
[16] ChunkingQualityMiddleware  ← Before ingestion: score chunk quality, re-chunk if low (NEW)
      │
      ▼
[17] GraphContextInjector       ← modify_model_request: inject KG facts into prompt (NEW)
```

### New Middleware (  additions explained)

**SemanticCacheMiddleware** — wraps every model call and checks Redis for a semantically similar prior query (embedding cosine similarity > 0.92 threshold). If hit, returns cached response instantly without calling the LLM. Massive cost savings on repeated document queries.

**RetrievalGroundingMiddleware** — after every agent response, verifies that each factual claim in the response has a corresponding citation in the retrieved context. If any claim is ungrounded, the middleware either strips it or triggers a re-retrieval loop before returning to the user.

**ChunkingQualityMiddleware** — scores each generated chunk for semantic coherence (using an embedding self-similarity metric). Low-scoring chunks (mid-sentence breaks, topic mixing) are re-chunked using the next strategy in the fallback ladder.

**GraphContextInjector** — before every model call, queries the NetworkX graph artifacts knowledge graph for entities mentioned in the user query, and injects the top-3 most relevant graph paths directly into the system prompt as structured facts.

---

## 🧠 4-Layer Memory System (Unchanged + Enhanced)

```
┌───────────────────────────────────────────────────────────────┐
│                      MEMORY SYSTEM                            │
│                                                               │
│  ┌─────────────────┐   ┌─────────────────────────────────┐   │
│  │  SHORT-TERM     │   │  LONG-TERM                      │   │
│  │  (Thread scope) │   │  (Cross-thread, per user)       │   │
│  │                 │   │                                 │   │
│  │  PostgresSaver  │   │  RedisStore + vector index      │   │
│  │  Full message   │   │  User preferences, domain       │   │
│  │  history +      │   │  expertise, learned facts,      │   │
│  │  tool results   │   │  past analysis summaries        │   │
│  └─────────────────┘   └─────────────────────────────────┘   │
│                                                               │
│  ┌─────────────────┐   ┌─────────────────────────────────┐   │
│  │  EPISODIC       │   │  PROCEDURAL                     │   │
│  │  (Event log)    │   │  (Behavioral adaptation)        │   │
│  │                 │   │                                 │   │
│  │  Timestamped    │   │  "User prefers bullet points"   │   │
│  │  events: what   │   │  "Always flag currency in USD"  │   │
│  │  happened, when │   │  Injected via ContextEditing    │   │
│  │  and in what    │   │  Middleware into system prompt  │   │
│  │  context        │   │  on every turn                  │   │
│  └─────────────────┘   └─────────────────────────────────┘   │
│                                                               │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  KEYVALUE CACHE (Redis Semantic Cache)                │   │
│  │  Cache LLM responses by embedding similarity          │   │
│  │  Threshold: cosine > 0.92 = cache hit                 │   │
│  │  Slashes cost 40–60% on repeated document queries     │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                               │
│  NEW: DOCUMENT MEMORY (  addition)                          │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  Persists extracted entities and KG from uploaded     │   │
│  │  files across sessions. User re-uploads detected      │   │
│  │  → incremental graph update, not full rebuild.        │   │
│  └───────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

---

## ⏳ Time Travel Engine

Every agent step auto-saves a LangGraph checkpoint. The Time Travel panel in the UI lets users:

| Action | What It Does |
|--------|-------------|
| **View history** | Browse every checkpoint with agent state, tool calls, and context |
| **Branch** | Fork from any checkpoint into a new thread |
| **Replay** | Re-run from a checkpoint with a modified prompt |
| **Audit** | Full traceable log of every middleware hook, tool call, memory read |
| **Rollback** | Undo a dangerous action (e.g., before a HITL-missed destructive tool call) |

New in  : **Document Version Branching** — if a user uploads a new version of a file, the system creates a checkpoint branch so the prior version's analysis is preserved and comparable.

---

## 📡 Streaming Architecture

```
LangGraph astream()
    │
    ├── stream_mode="messages"  → token-by-token LLM output → live text panel
    │
    ├── stream_mode="updates"   → node-level graph updates → agent activity feed
    │    Examples: "Ingestion Agent: parsing PDF..."
    │              "Graph Builder: 234 entities extracted..."
    │              "Stats Agent: running anomaly detection..."
    │
    └── stream_mode="custom"    → custom emitted events
         Examples: OCR progress (page 3 of 12)
                   Chunking strategy selected: "Semantic"
                   Cache HIT — returning cached response
                   PII detected and redacted
                   HITL pause: awaiting approval
```

---

## 🆕 My Toppings (Original Ideas Not in Any LangChain Doc)

### 🍕 Topping 1 — Document DNA Fingerprinting
Every uploaded document gets a semantic "DNA fingerprint" — an embedding of its structure, entity density, topic distribution, and language style. When a user uploads a new file, the system checks it against all prior document fingerprints. If a near-duplicate is detected (similarity > 0.95), it says: *"This looks very similar to 'Q2 Report' you uploaded 3 weeks ago. Should I compare them instead of reprocessing?"* — saving compute and surfacing implicit insight.

### 🍕 Topping 2 — Confidence Watermarking on Every Response
Every sentence in every agent response gets a **confidence score** rendered in the UI as a subtle color gradient (green → yellow → red). Confidence is derived from: retrieval similarity score × grounding check result × model temperature. Users see at a glance which parts of the answer are rock-solid vs speculative. Clicking any sentence jumps to the exact source span in the original document.

### 🍕 Topping 3 — Adversarial Reflection Agent (Devil's Advocate)
After any summary or analysis is generated, a hidden **Devil's Advocate Agent** generates 3 counterarguments or missing considerations — not shown by default, but accessible via a "Challenge This" button. Inspired by red-teaming methodology, it prevents users from over-trusting agent outputs and surfaces blind spots.

### 🍕 Topping 4 — Temporal Drift Detection
For users who repeatedly upload periodic reports (weekly sales, monthly financials), the system tracks **semantic drift over time** using embedding distance between document versions. When drift exceeds a threshold (topic shifts, new entities appear, tone changes), the Orchestrator proactively flags: *"This month's report introduces topics not seen before: 'supply chain disruption' and 'vendor X'. Want me to deep-dive these?"*

### 🍕 Topping 5 — Agent Consensus Voting
For high-stakes analyses (legal review, financial audit), multiple specialist agents independently analyze the same context and produce answers. A **Consensus Agent** then compares outputs, identifies disagreements, and reports: *"Stats Agent and SQL Agent disagree on total revenue by 12%. Here are both calculations."* This surfaces calculation errors, ambiguous data, and model inconsistencies that would otherwise be invisible.

### 🍕 Topping 6 — Lazy Graph Building (Cost-Aware)
Knowledge graph construction is expensive. The system implements a **two-tier graph strategy**: a lightweight "shallow graph" (just named entities + co-occurrence edges) is built instantly on upload; a deep graph (full relationship extraction via LangExtract-grounded graph extractor) is only built when the user asks a relational question. The GraphContextInjector Middleware detects when graph depth is needed and triggers on-demand deep graph expansion.

### 🍕 Topping 7 — Explainability Panel
Every response includes an expandable "How I Got Here" panel showing: which chunks were retrieved, which graph paths were traversed, which agents contributed, which middleware fired, and which memory layers were read. Zero black-box behavior — every decision is fully auditable without LangSmith.

---

## 🗂️ Complete Project Folder Structure

```
omni-analyst- /
│
├── agents/
│   ├── orchestrator.py          # LangGraph StateGraph supervisor
│   ├── ingestion_agent.py       # File parse + OCR routing
│   ├── extraction_agent.py      # LangExtract schema extraction
│   ├── graph_builder_agent.py   # NetworkX graph artifact builder
│   ├── chunking_strategy_agent.py # Dynamic chunking selection
│   ├── stats_agent.py
│   ├── visual_agent.py
│   ├── sql_agent.py
│   ├── summary_agent.py
│   ├── compare_agent.py
│   ├── audit_agent.py
│   ├── research_agent.py        # Web search for simple queries
│   ├── reflection_agent.py      # Quality review + Devil's Advocate
│   └── consensus_agent.py       # Multi-agent voting
│
├── middleware/
│   ├── pii_middleware.py
│   ├── summarization_middleware.py
│   ├── context_editing_middleware.py
│   ├── tool_selector_middleware.py
│   ├── todo_list_middleware.py
│   ├── model_call_limit_middleware.py
│   ├── tool_call_limit_middleware.py
│   ├── tool_retry_middleware.py
│   ├── llm_tool_emulator.py
│   ├── model_fallback_middleware.py
│   ├── hitl_middleware.py
│   ├── shell_tool_middleware.py
│   ├── filesystem_middleware.py
│   ├── semantic_cache_middleware.py    # NEW
│   ├── retrieval_grounding_middleware.py # NEW
│   ├── chunking_quality_middleware.py  # NEW
│   └── graph_context_injector.py      # NEW
│
├── ingestion/
│   ├── file_router.py           # Detect type, route to parser
│   ├── ocr/
│   │   ├── tesseract_engine.py
│   │   ├── surya_engine.py
│   │   ├── vision_llm_engine.py
│   │   └── ocr_orchestrator.py  # Confidence-based fallback
│   ├── parsers/
│   │   ├── pdf_parser.py
│   │   ├── excel_parser.py
│   │   ├── docx_parser.py
│   │   ├── csv_parser.py
│   │   └── pptx_parser.py
│   └── structure_recovery.py    # Hierarchy, tables, figures
│
├── chunking/
│   ├── semantic_chunker.py
│   ├── structural_chunker.py
│   ├── parent_doc_chunker.py
│   ├── late_chunker.py
│   ├── token_chunker.py
│   └── quality_scorer.py        # Score + re-chunk if low quality
│
├── extraction/
│   ├── langextract_runner.py    # Google LangExtract integration
│   ├── schema_registry.py       # Domain-specific extraction schemas
│   └── grounding_verifier.py    # Verify extractions vs source text
│
├── graph/
│   ├── graph_builder.py         # LangExtract-grounded graph extractor wrapper
│   ├── networkx_graph_client.py
│   ├── shallow_graph.py         # Fast entity co-occurrence graph
│   ├── deep_graph.py            # Full relationship extraction
│   └── graph_retriever.py       # Graph traversal for hybrid RAG
│
├── retrieval/
│   ├── hybrid_retriever.py      # Vector + BM25 + Graph + Metadata
│   ├── self_query_retriever.py  # Metadata self-querying
│   ├── parent_doc_retriever.py
│   ├── reranker.py              # ColBERT late-interaction reranking
│   └── hyde.py                  # Hypothetical Document Embeddings
│
├── memory/
│   ├── short_term.py            # PostgresSaver
│   ├── long_term.py             # RedisStore
│   ├── episodic.py
│   ├── procedural.py
│   ├── document_memory.py       # NEW: persist KG across sessions
│   └── kv_cache.py              # Redis Semantic Cache
│
├── time_travel/
│   ├── checkpoint_manager.py
│   ├── branch_router.py
│   └── doc_version_tracker.py   # NEW: file version branching
│
├── toppings/
│   ├── doc_dna_fingerprint.py   # Document similarity fingerprinting
│   ├── confidence_watermark.py  # Per-sentence confidence scoring
│   ├── devil_advocate_agent.py  # Adversarial reflection
│   ├── temporal_drift.py        # Periodic doc drift detection
│   └── explainability_panel.py  # Full decision audit trail
│
├── streaming/
│   ├── sse_server.py
│   └── stream_handler.py
│
└── api/
    └── main.py                  # FastAPI: upload, query, stream, time-travel
```

---

## 🔄 Full End-to-End Flow (Both Modes Combined)

```
User Input (query only OR query + file)
           │
           ▼
[1] PIIMiddleware scans query text
           │
           ▼
[2] Query Intent Classifier
    ├── SIMPLE_QUERY → skip to [8]
    └── DOCUMENT_QUERY → continue to [3]
           │
           ▼
[3] File Routing → OCR (if needed) → Structure Recovery
           │
           ▼
[4] Anti-Chunking Layer
    ChunkingStrategyAgent picks strategy → ChunkingQualityMiddleware validates
           │
           ▼
[5] LangExtract → Structured Entity Extraction (schema-grounded, source-traced)
           │
           ▼
[6] Graph Builder Agent
    → Shallow graph (instant) + Deep graph (on-demand)
    → NetworkX graph artifact storage
           │
           ▼
[7] Hybrid Retrieval
    Vector + BM25 + Graph Traversal + Metadata Self-Query + Parent-Doc + Reranker
           │
           ▼
[8] Episodic + Long-Term Memory retrieval → inject into context
           │
           ▼
[9] GraphContextInjector Middleware → inject KG facts into system prompt
           │
           ▼
[10] TodoListMiddleware → task decomposition if complex query
           │
           ▼
[11] LLMToolSelectorMiddleware → pick relevant tools for this turn
           │
           ▼
[12] Orchestrator routes to Specialist Agent Swarm (parallel where possible)
           │
           ▼
[13] Agents execute with full middleware stack (ModelFallback, ToolRetry, HITL)
           │
           ▼
[14] RetrievalGroundingMiddleware → verify all claims are source-grounded
           │
           ▼
[15] Reflection Agent → quality check → Consensus Agent (if high-stakes)
           │
           ▼
[16] Confidence Watermarking → per-sentence confidence scores
           │
           ▼
[17] Streamed response (tokens + agent activity + custom events) → UI
           │
           ▼
[18] LangGraph Checkpoint → Time Travel state saved
           │
           ▼
[19] Episodic Memory write → "User analyzed invoice_Q3.pdf at 14:32..."
```

---

## 💡 Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Agent Framework | LangChain v1 + LangGraph |
| Knowledge Graph | NetworkX + LangExtract-grounded entity/relation builder |
| Structured Extraction | Google LangExtract |
| OCR | Tesseract + Surya OCR + GPT-4o Vision |
| Chunking | LangChain `SemanticChunker`, `RecursiveCharacterTextSplitter`, custom |
| Vector Store | FAISS |
| Hybrid Search | BM25 lexical index + FAISS vector retrieval + ColBERT + RRF fusion |
| Reranking | ColBERT late-interaction reranking over FAISS candidates |
| Short-Term Memory | LangGraph `PostgresSaver` |
| Long-Term + Episodic | LangGraph `RedisStore` with vector search |
| KV Cache | Redis Semantic Cache |
| LLMs | GPT-4o (primary), Claude 3.5 (fallback), Gemini (LangExtract) |
| Streaming | LangGraph `astream()` → FastAPI SSE |
| Observability | LangSmith (all middleware hooks, agent steps, memory ops) |
| Frontend | React + TailwindCSS + confidence watermark overlay |
| Shell | `ShellToolMiddleware` persistent bash session |

---

## 🎯 What Makes   Different From Every Other Agent Project

1. **OCR is a first-class citizen** — not an afterthought. Three OCR engines with confidence-based fallback, page-level preprocessing, and bounding box metadata preserved through the entire pipeline.

2. **LangExtract gives grounded, schema-consistent extraction** — every entity is traced to its source character offset. Zero hallucination at the data extraction stage.

3. **The chunking problem is solved architecturally** — a dedicated agent picks the right chunking strategy per document type, validated by a quality scoring middleware. Bad chunks never reach agents.

4. **GraphRAG replaces naive vector search** — knowledge graph traversal finds multi-hop relationships that vector similarity can never find. The shallow/deep graph split keeps it cost-efficient.

5. **Both simple queries and document queries share one platform** — the same middleware stack, the same memory system, the same streaming UI. No switching between tools.

6. **Confidence watermarking makes trust visible** — users see exactly how sure each sentence is, with one-click jump to source evidence.

7. **Document memory persists the knowledge graph across sessions** — the graph you built from last month's contracts is still there. Upload a new contract and it extends the existing graph.

---

