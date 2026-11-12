# Design Decisions

Newest first. Each entry: context, decision, consequences.
Only choices with real alternatives are recorded here.


---

## 012. Answers: three statuses, chunk-ID citations, code-side validation
*2026-11-11 · Phase 4*

**Context**
With a binary `answerable` flag, q01 ("why did net income decrease?") returned "no evidence"
although the retrieved p30 table showed the decrease itself; only the cause was missing.
Users would wrongly conclude the report has no answer.

**Decision**
- Structured output (OpenAI Structured Outputs, Pydantic schema):
  `status` (answered / partial / no_evidence), `sentences[{text, citations, inferred}]`, `missing`.
- `partial`: answer the supported part, state what is missing, never fill gaps with guesses.
- The LLM cites chunk IDs only; code maps IDs to pages and bboxes.
- Code validates every citation against the retrieved chunk IDs.
- Every eval run saves all answers to `evals/runs/` for later review and judge scoring.

**Results** (golden set, k=5, model `gpt-6-luna`)
- status correct 10/10, citations valid 10/10, evidence hit 0.64 (retrieval quote recall@5: 0.69).
- The gap is q05 only: a redundant source was retrieved but not cited.
  Answer quality is currently capped by retrieval.
- q01 now returns `partial` and names "taxes or non-operating items" as the missing drivers.
- q03 returned `answered` with no gold evidence retrieved: [확인 결과]

**Consequences**
- Format and citation-ID checks cannot detect a sentence that cites a real chunk
  but misstates it. A faithfulness check is needed (Phase 5).
- q03 returned `answered` with gold evidence 0/2, but the answer was correct:
  it cited the EPS note (p60), which repeats the diluted share counts the golden set
  lists only on p47. The same fact often appears in several places (statements, notes, MD&A);
  golden evidence needs alternatives (Phase 5). The answer stopped at "fewer shares"
  and did not reach the buybacks behind it.

---

## 011. Retrieval baseline: plain dense search; "why" failures need decomposition, not tuning
*2026-11-11 · Phase 3*

**Context**
First golden-set run on `chunks_llamaparse_v2_2` (317 chunks, bge-small, FLAT/COSINE, k=5, 9 answerable questions).

| setting | page recall@5 | quote recall@5 |
|---|---|---|
| baseline | 0.74 | 0.69 |
| bge query prefix | 0.74 | 0.69 |
| body only | 0.74 | 0.69 |
| prefix + body only | 0.74 | 0.69 |

- Raising k from 5 to 20 lifted quote recall only from 0.69 to 0.75 (q01's tax paragraph, rank 20);
  q03, q04 and q08 evidence stayed out of reach (ranks 23–68). Page recall did not move (0.74),
  so page-level scoring could not see this gain at all.
- No quote was missing from the index: parsing and chunking lost nothing; all misses are retrieval misses.
- numeric (q05–q07) and single-cause why (q02) were fully retrieved.
- Multi-step why questions failed on their cause evidence: q01 tax paragraph (rank 20),
  q03 share count and buybacks (rank 23, 68), q04 cash-flow investment gains (rank 50).
  In each case the cause is described in different words than the question.

**Decision**
- Use plain queries without the bge prefix (no gain; misses ranked lower).
- Keep the body-only filter available but off by default (no effect on this set).
- Do not tune retrieval further for "why" questions. Phase 6 will decompose the change
  numerically first and generate cause-specific queries.

**Consequences**
- 9 questions are too few to separate settings reliably; expand to 30–50 in Phase 5.
- Chunking v1 vs v2.2 comparison (promised in 010) is still pending.

---

## 010. Chunking v2: merge across pages, split long tables, attach captions
*2026-11-11 · Phase 3*

**Context**
v1 (one block = one chunk, LlamaParse output) produced 881 chunks.
- 22 exceeded the embedding model's 512-token limit, so their tails were silently truncated.
  16 were tables, incl. the p51 cash flow statement (1,001 tokens), whose later rows
  (e.g. `Common stock repurchases`, evidence for golden q03) were unsearchable.
- 236 were heading-only. Measuring length on `embed_text` hid this: the context header
  alone is ~20 tokens, so short-chunk checks must measure `text`.

**Decision**
- Headings are not chunks; they become metadata and the context header.
- Consecutive text/list blocks under one heading merge up to ~400 tokens, across page boundaries.
- Long tables split into row groups; each piece repeats the header row and the latest section-label row.
- Short captions before a table (ending with ":" or "(In millions ...)") are attached to every piece.
- Table markdown is compacted: separator dashes are one token each, and wide tables
  spent 100+ tokens per piece on them.
- A guard re-splits any table whose actual `embed_text` exceeds 500 tokens.
- Body and exhibits are never merged; "See accompanying Notes" boilerplate is dropped.

**Rationale**
For a tool that helps users reason about *why* results changed, richer context that lets
users judge for themselves is worth more than narrower, more precise chunks.

**Consequences**
- v1 → v2.2: 881 → 317 chunks; >512 tokens: 22 → 0; short chunks: 236+ → 16,
  mostly "None."-type items and tables LlamaParse labeled as text.
- Larger chunks mean wider highlights; sentence-level highlighting can refine this later.
- Section-label carry-over can attach a stale label after a section ends.
- To be validated against v1 with recall@5 in 3-4.

---

## 009. Install milvus-lite >= 3.0 explicitly
*2026-11-10 · Phase 3*

`pymilvus[milvus-lite]` silently skips milvus-lite on Windows (the extra is platform-restricted).
milvus-lite 3.0 supports Windows, so it is declared as a direct dependency.
Embedding model: `BAAI/bge-small-en-v1.5` (384 dims, local, free).

---

## 008. LlamaParse as primary parser; PyMuPDF kept as baseline
*2026-11-08 · Phase 2*

**Context**
Pages 30, 32, 47, 80 of the Applied Materials FY2025 10-K were parsed with
PyMuPDF (local, free) and LlamaParse v2 (`cost_effective` tier, cloud, paid per page).

| | PyMuPDF | LlamaParse |
| p47 `Diluted 808 834 845` | label "Weighted average number of shares" ends the previous block | same table: section row directly above, year header row at top |
| Blocks/items per page (p30, p32, p47, p80) | 20, 20, 9, 11 | 14, 13, 7, 11 |
| After noise filter | 17, 17, 7, 9 | 12, 11, 5, 9 |
| Element types | none | heading, text, table, header, footer |
| Tables | split into text blocks; row labels and values can land in different blocks | one item per table with rows; header row (`Fiscal Year | 2025 | 2024 | 2023`) kept with the data |
| p47 `Diluted 808 834 845` | label "Weighted average number of shares" ends the previous block | [확인 결과로 채우기] |
| Header/footer detection | none | inconsistent: some labeled header/footer, others heading/text |
| Cost, latency | free, instant, deterministic | credits per page, network call |

**Decision**
- Use LlamaParse for ingestion, because table structure is preserved.
- Keep PyMuPDF as the baseline to measure against in Phase 3.
- Both parsers emit one common schema: `{page, width, height, blocks: [{id, type, bbox, text}]}`,
  with bbox converted to `[x0, y0, x1, y1]` in page points (origin top-left).
- Normalize text before rules (strip markdown `#`, HTML tags, non-breaking spaces);
  apply the same noise filter to both parsers.
- Split LlamaParse into `fetch` (API call, saves raw response) and `convert` (local),
  so converter changes never spend credits.

**Consequences**
- This comparison is qualitative and covers 4 pages. The decision will be re-checked
  quantitatively in Phase 3/5 (retrieval recall@5 per parser on the golden set).
- Only the first bbox of each item is used; items spanning several boxes will highlight partially.
- Ingestion now depends on an external paid API; PyMuPDF remains the fallback.
---

## 007. Noise filtering: content plus position; table fragments are kept
*2026-11-07 · Phase 2*

**Context**
The PyMuPDF baseline extracts blocks that carry no retrievable information:
a "Table of Contents" link at the top of every page, page-number footers, and blocks
containing only non-breaking spaces. It also splits tables, leaving column headers
(e.g. `"2025\n2024"`) as separate blocks.

**Decision**
- Remove three noise types: running header (text match + y0 < 50),
  page-number footer (text equals the page number), and empty blocks.
- Do not use position-only rules: footer y varies from 431 to 651 because
  short pages are not padded to full height.
- Keep table header fragments. They are not noise; they are detached from their rows.

**Consequences**
- Filter rules are specific to this document's rendering and must be checked
  on each new source.
- Detached headers will still pollute retrieval: a chunk containing only "2025 2024"
  matches almost any question mentioning 2025. Fixing this requires a parser that
  understands table structure, evaluated in decision 008.

---

## 006. Source PDFs: record provenance, do not commit, fetch from private storage in CI
*2026-11-06 · Phase 1*

**Context**
The golden eval set references page numbers in one specific PDF of the
Applied Materials FY2025 10-K. To verify quotes in CI, CI needs that exact file.

| Source | Official | Fetchable by CI | Same file as golden |
|---|---|---|---|
| Company IR site (PDF) | Yes | No (blocked by bot protection) | Yes |
| SEC EDGAR | Yes (original filing) | Yes (requires User-Agent per SEC Fair Access policy) | No (HTML, not PDF) |

**Decision**
- `data/sources.json` records `source_url`, `filing_url`, and a `sha256` checksum.
- PDFs are never committed: they are copyrighted and large.
- CI will fetch the PDF from private storage (S3, Phase 8) and verify the checksum
  before running quote checks. Until then, CI runs structural checks only.

**Consequences**
- The checksum ties golden page numbers to one file version; a different rendering
  of the same filing may paginate differently.
- Provenance and fetchability are separate concerns. Every new source must record both.

---

## 005. Golden answers separate what the report states from what is inferred
*2026-11-06 · Phase 1*

**Context**
Some answers come directly from MD&A text; others must be derived.
For example, the EPS increase (q03) follows from share counts and is never explained in the report.

**Decision**
- Each answer marks inferred reasoning explicitly ("inferred from…", "this judgment is an inference").
- One `unanswerable` question is included, whose correct answer is "no evidence in this report."

**Consequences**
- This distinction becomes the basis of the faithfulness metric (Phase 5):
  the system must not present an inference as a reported fact.
- The `unanswerable` case tests that the system refuses instead of inventing a figure.

---

## 004. Evidence quotes are short, verbatim, and validated against the PDF
*2026-11-06 · Phase 1*

**Context**
Quotes will later be used to score retrieval automatically,
so a quote that does not exist in the extracted text breaks scoring silently.

**Decision**
- `evals/validate.py --pdf` checks that every quote exists on its page (whitespace-normalized).
- Sentence quotes are preferred over table rows.

**Consequences**
- Table rows proved fragile: PyMuPDF extracts `7 % 4 %` where the page shows `7% 4%`.
  Extraction differences between parsers will matter again in Phase 2.

---

## 003. Golden set schema
*2026-11-06 · Phase 1*

**Decision**
- Four question types: `why`, `numeric`, `compare`, `unanswerable`.
- `page` is the 1-based PDF page index, not the printed page number.
- `numbers` are in USD millions unless the key says otherwise (`_pct`, `_eps`, `_m` for shares).

**Consequences**
- Question types double as ground-truth labels for evaluating the query router (Phase 6).
- The PDF viewer navigates by index, so evidence highlighting uses the same page value.

---

## 002. Build the eval set before any pipeline code
*2026-11-06 · Phase 1*

**Context**
Without a fixed target, changes to parsing, chunking, or prompts can only be judged by eyeballing.

**Decision**
Write 10 Q&A pairs by hand from the Applied Materials FY2025 10-K before writing the pipeline.
Expand to 30–50 in Phase 5.

**Consequences**
Every later change is measured against the same questions.
Writing answers by hand also built the domain knowledge needed to design the "why" analysis.

---

## 001. Monorepo with uv for Python dependencies
*2026-11-05 · Phase 0*

**Context**
The project has a Python backend, a frontend, an eval suite, and docs, built by one person.

**Decision**
- One repository with `backend/`, `frontend/`, `data/`, `evals/`, `docs/`.
- uv for Python environments and dependencies, instead of pip + venv or Poetry.

**Consequences**
- One lock file reproduces the same environment locally and in deployment.
- A single repo keeps the architecture readable for reviewers; it can be split later if needed.