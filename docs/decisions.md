# Design Decisions

Newest first. Each entry: context, decision, consequences.
Only choices with real alternatives are recorded here.

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