# T07 — Automotive knowledge pack ingestion and RAG (`search_knowledge`)

**Priority:** Must · **Milestone:** M3 Brain & Notion · **Depends on:** T01, T06 · **Estimate:** 2–3 days

## Why

- "Knowledge" row: car engineering (**EV, chassis, NCAP, ADAS, regulation, BOM**) and BA outputs; **says "not sure" instead of inventing**.
- "Knowledge (RAG)" part: **searches the automotive pack (specs, parts, BOM, suppliers) before answering; cites the source doc**.
- Required tool: **`search_knowledge(query)`**, which retrieves from the automotive / parts pack.
- Answer rule: **numbers always come with assumptions or a source doc** (C7). Citations come from here.
- Not allowed: hard-coded answers (C4). Knowledge must come from retrieval over the pack, never canned replies.

## What

**In scope**
- Loaders for the provided **fictional automotive pack** (Falcon specs, parts list / BOM, supplier list). The format is unknown until delivery (open questions Q27–Q28), so support PDF, Markdown/text, DOCX, CSV and XLSX.
- Chunking with source metadata (file, page, section, table row).
- Self-hosted embeddings plus Qdrant vector store; hybrid lexical + dense retrieval.
- `search_knowledge(query)` returning ranked chunks with citations and a confidence signal for "not sure".
- Structured extraction of tabular data (parts, BOM, suppliers, variant specs) into SQLite for T08's exact calculations.
- Idempotent ingest command; a synthetic dev fixture pack for tests.

**Out of scope**
- BOM maths and comparisons (T08). Guardrail decisions (T13 uses this task's scores).

## Details

### Ingest
```
data/pack/* → loader per type → Document{text, metadata}
           → chunker → Chunk{id, text, doc, section, page, row, hash}
           → embed (local) → Qdrant collection "pack"
           → (tables) → SQLite tables: parts, suppliers, bom_lines, variant_specs
```
- **PDF:** `pypdf` or `pdfplumber` (tables). Keep page numbers.
- **Markdown/text:** split on headings, keeping the heading path as `section` (e.g. `Battery > Pack options`).
- **DOCX:** `python-docx`, split on headings.
- **CSV/XLSX:** `pandas`. Each row becomes its own chunk rendered as `"col: value; col: value"` with the header repeated, so a row is self-contained. The table also goes to SQLite (T08).
- Chunk size ~300–500 tokens with ~50 overlap for prose. Tables are chunked per row (or small row groups).
- **Idempotent:** a content hash per file. Re-running skips unchanged files and replaces changed ones. The CLI is `python -m ba_agent.knowledge.ingest --pack /data/pack`, and the agent container runs it at start if the pack changed (T15).
- Record an ingest manifest (files, chunk counts, tables found) and log it, so it's obvious if a file failed to parse.

### Embeddings and store
- Embedding model `embeddings.model`, default `BAAI/bge-small-en-v1.5` via `sentence-transformers` on the GPU (small and fast). Alternatives: `bge-m3`, or `nomic-embed-text` through Ollama. Pick by recall on the eval set below.
- Qdrant (suggested in the PDF) runs as a compose service with a persistent volume. Chroma (embedded) is an acceptable alternative.

### Retrieval
- **Hybrid:** dense similarity plus BM25 lexical (part numbers, supplier names and units like "75 kWh" match better lexically). Fuse with Reciprocal Rank Fusion (RRF). Use Qdrant sparse vectors, or `rank_bm25` in-process (the pack is small).
- Optional reranker (`BAAI/bge-reranker-base`) if it improves eval recall within ~150 ms.
- `top_k = knowledge.top_k` (default 5).
- **Confidence:** each result carries the dense cosine score. If the best score is below `knowledge.min_score` (tune on the no-answer eval set), return `results=[]` with `"no_match": true`. The agent must then say "not sure" (T13).

### Tool contract
```python
search_knowledge(query: str) -> {
  "results": [
    {"text": "...", "source": "falcon_specs.pdf p.3 §Battery > Pack options",
     "doc": "falcon_specs.pdf", "score": 0.82}
  ],
  "no_match": false
}
```
- Citation shown in chat: `(src: falcon_specs.pdf p.3)`. Short form, because replies are ≤ 6 lines.

### General engineering knowledge vs pack facts (policy shared with T13)
- **Falcon-specific facts (specs, parts, prices, suppliers, lead times)** come only from the pack, with a citation. Never from model memory.
- **General concepts** ("what does NCAP test?", "LFP vs NMC chemistry trade-offs", "what is MoSCoW?") may come from the model's general knowledge, labelled "general knowledge". Any number in such an answer must be marked as an assumption or typical value (C7).
- Optional: add a small, **reviewed** supplementary primer (e.g. `data/primer/` with short notes on NCAP, ADAS levels, EV regulation, user-story and MoSCoW format) so even general answers can cite something. Keep it separate from the TechSol pack, label it as ours, and don't put any Falcon numbers in it.

### Dev fixture pack
Until the real pack arrives, use `tests/fixtures/pack/` (a small synthetic Falcon-like spec, parts CSV and supplier CSV). Use it **only in tests**. Its numbers must differ from the real pack so a test can never pass because of leaked real data, and so the fixture can never be served as "answers" (C4).

## Acceptance criteria

- [ ] Every file in the real pack ingests. The manifest shows chunk counts and the extracted tables. Zero silent failures.
- [ ] Every result carries a human-readable `source`.
- [ ] Retrieval eval (on the real pack, ≥ 30 questions written by us): the correct source is in the top 5 for ≥ 90% of answerable questions.
- [ ] No-answer eval (≥ 15 questions about things not in the pack): `no_match` for ≥ 90% of them.
- [ ] `search_knowledge` p95 ≤ 300 ms on the VM.
- [ ] Re-ingesting an unchanged pack is a no-op. Changing one file updates only that file's chunks.
- [ ] Parts, suppliers, BOM and variant tables are available in SQLite for T08.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T07-U1 | unit | Loaders on fixture PDF / MD / DOCX / CSV / XLSX | Text + metadata (page/section/row) correct |
| T07-U2 | unit | Chunker sizes, overlap, heading paths, table-row rendering with header | As specified |
| T07-U3 | unit | Idempotent ingest (hash skip, changed-file replace) | Chunk counts stable; no duplicates in Qdrant |
| T07-U4 | unit | RRF fusion ordering on synthetic scores | Expected order |
| T07-U5 | unit | `no_match` when best score < threshold | `results == []`, `no_match == true` |
| T07-I1 | integration | Retrieval eval set (answerable) on the real pack | Recall@5 ≥ 90% |
| T07-I2 | integration | No-answer eval set | `no_match` ≥ 90% |
| T07-I3 | integration | Exact-term queries (part numbers, supplier names) | Hybrid beats dense-only; correct row in top 3 |
| T07-P1 | performance | 100 queries on the VM | p95 ≤ 300 ms |

## Dependencies

- T01 (config), T06 (optional LLM query rewriting; GPU budget).
- Enables T08 (structured tables), T12 (tool), T13 (no-match signal).

## Related resources

- Qdrant docs: https://qdrant.tech/documentation/ · Hybrid queries: https://qdrant.tech/documentation/concepts/hybrid-queries/
- Chroma docs: https://docs.trychroma.com/
- sentence-transformers: https://sbert.net/
- BGE small: https://huggingface.co/BAAI/bge-small-en-v1.5 · BGE reranker: https://huggingface.co/BAAI/bge-reranker-base
- rank_bm25: https://github.com/dorianbrown/rank_bm25
- Reciprocal Rank Fusion (Cormack et al., 2009): https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf
- pdfplumber: https://github.com/jsvine/pdfplumber · pypdf: https://pypdf.readthedocs.io/ · python-docx: https://python-docx.readthedocs.io/
- Euro NCAP (background for the optional primer): https://www.euroncap.com/
- UNECE vehicle regulations (WP.29, background for the optional primer): https://unece.org/transport/vehicle-regulations
