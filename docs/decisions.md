# Design Decisions

Newest first. Each entry: context, decision, consequences.
Only choices with real alternatives are recorded here.


---

## 033 Registry in SQLite; company is a hard filter

Date: 2026-11-27
Status: accepted (extends 019)

Context: Phase 9 adds uploads into per-company folders. The registry (data/sources.json) would be
rewritten by the server while it runs: two uploads finishing together can lose one entry, and a crash
mid-write corrupts the file. Answers in one company's folder must never use another company's chunks.

Decision:
- `data/app.db` (SQLite) with `companies` and `documents(status, error, UNIQUE(company_id, sha256))`.
  Each change is one row in a transaction. `data/sources.json` stays as the committed seed.
- Same file in the same folder returns the existing document (no second parse). Same doc_id with a
  different file is an error, never silently accepted.
- Only `ready` documents are visible to the router and the dashboard.
- Every chunk stores `company_id`, taken from the registry at index time. Retrieval always adds
  `company_id == X` in code, independent of the router (which may be an LLM). The router also only
  sees that company's documents. Company ids are validated before going into the filter expression.
- New collection `docs_v3`; `docs_v2_3` kept for rollback and comparison.

Consequences: existing modules read the same dict shape and did not change. The facts table keeps
doc_id only; company comes from a join, so the fact is stored once.

---

## 032 CI installs light test dependencies

Date: 2026-11-26
Status: accepted (extends 018)

Context: CI installed only pytest. `classify.py` (Phase 7) imports pydantic, so test collection failed
on the last two pushes. API and highlight tests were silently skipped (`importorskip`).

Decision: CI installs pytest, pydantic, fastapi, httpx, pymupdf. Still no embedding model, Milvus,
OpenAI or PDFs. Verified by running the suite in a fresh virtual environment with only these packages.

Consequences: 81 tests run in CI, including API endpoints and highlight geometry on a generated PDF.
A new third-party import in tested code must be added to CI in the same PR.

---

## 031 Viewer and dashboard UX: no flicker, no lost state, resizable panes

Date: 2026-11-26
Status: accepted

Context: First user test of the dashboard. Clicking waterfall bars made the PDF flicker several times
before settling. Switching tabs reset the dashboard. The left pane's width was fixed.

Decision:
- Double-buffered PDF pages: a new page or width renders hidden and is swapped in on `onRenderSuccess`.
  The previous page stays visible meanwhile. Highlight-only changes on the same page do not re-render.
- Break the resize feedback loop (scrollbar appears → narrower → re-render → scrollbar disappears …):
  `scrollbar-gutter: stable`, width in 8 px steps, applied 150 ms after resizing stops.
- Tabs are hidden, not unmounted. Summaries are cached per document. The selected bar is remembered
  by a document-independent key (e.g. `tax_provision`), so switching FY2025 ↔ FY2024 highlights the
  same item's row in each report.
- Draggable divider (pointer capture, ←/→ keys), saved in localStorage. Zoom 75–200%.

Consequences:
- Comparing one driver across years is now one click. This came from the user, not the original plan.
- The dashboard is 10-K only. Uploads and company workspaces move to Phase 9.

---

## 030 API returns highlight boxes; React viewer draws them over react-pdf

Date: 2026-11-25
Status: accepted

Context: The product promise is "click an answer sentence, see the evidence in the PDF".
Chunks already store block boxes (Phase 2). Text search in the rendered PDF would not match LlamaParse
markdown text.

Decision:
- FastAPI `POST /ask` returns sentences, citations, and one entry per cited source with highlights.
  Models load once at startup.
- Two highlight levels:
  - block: the chunk's boxes, light
  - exact: numbers from the citing sentences, searched only inside the chunk's blocks with
    PyMuPDF `search_for`, then widened to the table row, dark
  Computed sources highlight the statement rows behind their "A -> B" values.
  Contributions and years are never searched.
- Coordinates stay in PDF points with a top-left origin (PyMuPDF).
  The viewer scales by rendered width (px) / original page width (pt, from react-pdf `onLoadSuccess`).
- Frontend: Vite + React + TypeScript + react-pdf, no SSR (pdf.js is browser-only).

Consequences:
- First check on q01: the bridge source highlighted 9 rows on p47. The first version also lit the table
  header because "fiscal 2024 -> 2025" looked like a value pair; fixed and tested.
- Numbers the PDF does not contain (e.g. a computed +$422 change) highlight nothing, by design.

---

## 029 Fail fast when a collection lacks registered documents

Date: 2026-11-24
Status: accepted

Context: The first Phase 7 answer run used the default collection `docs_v2_2`, which has no proxy chunks.
The router sent q51–q56 to the proxy, retrieval returned nothing, and the model honestly answered
"no evidence". There was no error, only six plausible wrong answers.

Decision:
- `answer.py` and `eval_answer.py` default to `docs_v2_3`.
- Before running, check that every document in `data/sources.json` has at least one chunk in the
  collection. Stop with the missing doc_ids otherwise.

Results (Phase 7 final: docs_v2_3, k=10, cascade router, bridge decomposition, 58 questions):
| status ok | citations | numbers | evidence hit | faithfulness |
| 58/58 | 58/58 | 58/58 | 0.83 | 97% supported (72/74), 0 unsupported |

Consequences:
- The mistaken run stays in results_answer.md with a note.
- Faithful is not the same as correct. q51 answered with the proxy's "total direct compensation"
  ($29,481,370) instead of the Summary Compensation Table total ($29,649,352). It was judged supported
  because the table states it. None of the current metrics checks the answer value itself.
  Next: an answer-correctness check against golden `numbers`.

---

## 028 Quotes must exist in a chunk, checked with the evaluator's normalization

Date: 2026-11-24
Status: accepted

Context: `--check-quotes` compares quotes with PyMuPDF page text. PyMuPDF reads tables column by column,
LlamaParse row by row. A quote taken from PDF text ("29,649,352 27,799,212") passed validation but could
never match a chunk, so q53 scored 0 while its chunk was in the top 10.

Decision:
- `validate.py --check-chunks`: every evidence item must match at least one chunk (original location or
  an alternative), and every alternative must match a chunk.
- The normalization lives in `evals/textnorm.py`, shared by the validator and the evaluator.
- Skip documents without chunk files (CI has none).

Consequences:
- q53/q57 alternatives were rewritten as bare amounts (an 8-digit amount names one fact in the proxy).
  The 0.86 → 0.88 change from this was a golden fix, not a system change (noted in results.md).
- The first run also found a dead alternative in q21 (still to fix).
- q44's quotes do match chunks, so its 0 is a real ranking problem, not a quote problem.
- 2026-11-26: also normalize "$ 507" vs "$507" (q21's alternative never matched LlamaParse tables).
- 2026-11-26: also normalize "$ 507" vs "$507" (harmless, kept). q21's dead alternative was a
  different cause: a segment table parsed as text in column order, so the row never appears
  contiguously. Rewritten as a bare amount, like q53/q57.
  
---

## 027 Cascade classifier: rules first, LLM only when the rules are unsure

Date: 2026-11-24
Status: accepted

Context: Regex routing missed q54. Adding "KPMG" to the word list would fit the test, not the users.
An LLM classifier understands new phrasings but costs tokens and about 2.7 s per call.

Decision:
- `classify.py` has three modes, each returning {doc_types, question_type, reason}.
  - rule: also reports whether it is confident.
  - llm: structured output.
  - cascade: rules first; the LLM is called only when the rules are unsure.
- The rules are unsure when neither proxy nor financial words match (a default guess),
  or when both match (the expensive two-document path).
- Document type: cascade. Question type: rules. Year selection: rules.

Results (58 questions; question type scored on 52, unanswerable excluded):
| mode | doc type | question type | LLM calls | tokens | time |
| rule | 57/58 | 47/52 | 0 | 0 | 0 s |
| llm | 58/58 | 46/52 | 58 | 24,331 | 157 s |
| cascade | 58/58 | 47/52 | 16 | 6,738 | 41 s |
End to end (retrieval, k=10): q54 0.00 → 1.00; evidence documents covered 52/52; quote recall 0.88 → 0.90.

Consequences:
- Question type has no single right label. All three modes disagree with the golden label on the same
  two-intent questions (q40, q41, q45: "how much and why", "what and why").
  The LLM also read "values for FY2025 and FY2024" as compare.
  So judge the router by downstream scores, not by label accuracy. Today the type only gates bridge
  decomposition, and none of the disagreements is a net income or EPS question.
- 16 of 58 questions (28%) reach the LLM, mostly 10-K questions whose words are not in the financial
  list (DSO, RD&E, risk). Widening the list from the 10-K's own section names would cut calls further.

---

## 026 Proxy statement added; router picks document type before year

Date: 2026-11-24
Status: accepted

Context: The 2026 proxy (DEF 14A) covers fiscal 2025, the same `fiscal_year` as the FY2025 10-K.
The router mapped years to documents with one dict ({fiscal_year: doc_id}). With two documents for
2025, the later entry silently overwrote the earlier one: no error, wrong document.
The bridge's report lookup had the same trap.

Decision:
- Register the proxy as `amat-def14a-2026` (doc_type "DEF 14A", fiscal_year 2025, no exhibits).
  Index it in a new collection `docs_v2_3` (FY2025 10-K, FY2024 10-K, proxy: 1,008 chunks).
  Keep `docs_v2_2` for comparison.
- Route in two steps:
  1. Document type: proxy words → DEF 14A; financial words → 10-K; both → both; neither → 10-K.
  2. Year within each type: 10-K takes Y and Y+1; proxy takes Y.
  Years are looked up per type. Bridges read 10-Ks only.
- Golden q51–q58 (compensation, pay ratio, auditor fees, say-on-pay, nominees, one cross-document,
  one unanswerable).
- Evaluator labels documents K25 / K24 / PX26 (the fiscal year alone is ambiguous).

Results (docs_v2_3, k=10, decompose):
- The original 45 questions are unchanged line by line. The router never opens the proxy for them.
  When the FY2024 10-K was added unfiltered in Phase 6, recall dropped 0.83 → 0.73.
- Rule router: evidence documents covered 51/52. The miss is q54 ("fees paid to KPMG", no proxy word).

Consequences:
- Golden page numbers are PDF page indexes, not printed page numbers (the proxy is offset by 16).
  The dashboard should show both.
- The proxy repeats some figures (net income in Pay versus Performance) and reuses numbers with
  different meanings (91% say-on-pay vs 91% of CEO pay in long-term awards). Both are tagged in golden.

---

## 025 EPS bridge (Shapley), cash flow facts, and decomposition only for "why" questions

Date: 2026-11-23
Status: accepted

Context: q03 asks why diluted EPS rose while net income fell. The cause (share repurchases) is on the
cash flow statement, ranked 68th by retrieval.

Decision:
- Extract 11 cash flow rows (by last label, nested sections).
  Check that net income matches across the income and cash flow statements.
- EPS bridge: change = net income effect + share count effect.
  The split depends on order (the interaction term goes to whichever changes second),
  so use the average of both orders (Shapley). FY2025: -0.218 / +0.273 (orders: -0.215/+0.270, -0.222/+0.277).
  Allow 0.02 against reported EPS (cents and millions are rounded).
- EPS "why" questions:
  - fixed seats: income statement + cash flow chunks
  - queries: share repurchases + the top net income driver
  - computed source: `calc:eps:...`
- Decomposition runs only for "why" questions. Applying it to q50 ("how much did EPS grow")
  dropped it from 1.00 to 0.00: fixed seats pushed out the original answer.

Results (k=10, rule router, 45 scored):
| | quote recall | decomposed subset |
| net income decomposition | 0.89 | q01, q42 = 1.00 |
| + EPS, why-only | 0.90 | q01, q03, q42 = 1.00 |
q03: repurchases row 68th → slot 2. Answer evidence 2/2.

Consequences:
- Which processing a question gets depends on its type. A regex guards this for now;
  the question-type router (7-5) should own it.
- q04 and q08 remain at 0.50: their causes are outside the net income and EPS bridges.

---

## 024 Bridge as a citable computed source, plus a code check for numbers

Date: 2026-11-22
Status: accepted

Context: Bridge contributions (e.g. tax -1,298) appear in no report chunk, so an answer that cites
them could not be verified. The judge would mark them unsupported, or the LLM would mark them inferred.

Decision:
- The bridge goes into the context as a COMPUTED source (`calc:bridge:<doc>:<year>`), generated by code.
  It lists `source_chunks` (the income statement chunk), so citing it traces to report rows for highlighting.
- Prompt: COMPUTED sources give amounts, not reasons. Reasons must cite report chunks.
  Name offsetting contributors.
- Judge: `calc:` numbers count as facts. Causes still need a report source.
  Run records store the computed text.
- Code check before the judge: every number in a non-inferred sentence must appear in its cited sources
  (years and filing names like 10-K excluded).

Results (50q, k=10, rule router):
| | baseline | + bridge |
| status ok | 50/50 | 50/50 |
| q01 / q42 evidence | 1/4, 1/4 | 4/4, 4/4 |
| faithfulness supported (bridge run) | – | 97% (65/67), 0 unsupported |
| sentences citing calc: | – | 6/6 supported |
| planted calc errors caught | – | 3/3, control 1/1 |

Consequences:
- The number check's first run flagged "10" from "10-K" in 6 answers: a bug in the check, not in the answers.
  After the fix, no rounded or invented numbers.
- Run-to-run noise: q29 and q38 changed with identical context. 1–2 question differences are within noise.
- The judge's computed-source test has only 4 cases.

---

## 023 Bridge-driven query decomposition for net income questions

Date: 2026-11-22
Status: accepted (coverage 0.8 is an unvalidated default)

Context: "Why did net income change" questions failed retrieval since Phase 3.
The evidence uses different words (deferred tax remeasurement) than the question (net income),
and k=10 alone did not help (the tax sentence stayed at rank 20).

Decision: For net income change questions (not EPS):
- Compute the bridge from the year-Y report (decision 022).
- Pick drivers by cumulative |contribution| up to 80% (min 1, max 4).
  The share uses the sum of |contributions|, not the net change, because drivers offset.
- One report-style query per driver (HyDE), using only generic 10-K vocabulary.
- Seats within a fixed budget k=10:
  1. the income statement chunk the bridge used (fixed seat, no search)
  2. the original question's top 4
  3. each driver's top hit
  4. round-robin fill

Results (k=10, rule router, 45 scored; baseline re-run after adding alternatives):
| setting | subset q01,q42 | all |
| baseline k=10 | 0.25 | 0.86 |
| v1: drivers first, no fixed seat | 0.38 | 0.86 |
| v2: fixed seat + keep original 4 | 1.00 | 0.89 |

Consequences:
- v1 found the tax sentences but pushed out the original question's results; v2 fixed the seat order.
- Numeric evidence comes from the facts table, not retrieval.
- Same chunk budget as the baseline, so LLM cost is unchanged.
- Limits: n=2; only net income questions; templates untested on other companies.

---

## 022 Financial numbers in a SQLite facts table, with arithmetic checks

Date: 2026-11-18
Status: accepted

Context: "Why did profit change" needs exact numbers. LLMs read and add numbers unreliably,
and retrieval cannot see that the tax line is the main driver of a net income change.

Decision: Extract the income statement from LlamaParse table chunks into SQLite
`facts(doc_id, statement, item, label, period, value, unit, page, chunk_id)` (long format).
- `item` is a normalized key; `label` keeps the original wording.
  Different labels for the same concept share a key
  (FY2024 "Severance and related charges" and FY2025 "Restructuring charges" → `restructuring`).
- Section label rows prefix the rows below them ("Earnings per share: Basic").
- Five arithmetic checks (gross profit, total opex, operating income, pretax, net income),
  tolerance 1 (USD millions).
- `chunk_id` per number, so a number can be highlighted like a quote.

Consequences:
- The first FY2024 run failed one check (2022 total opex off by 4).
  The cause was an unmapped label holding a reversal of (4). Checks catch mapping gaps, not just parse errors.
- FY2024 net income is 7,177 in both reports (segments were recast, consolidated totals were not).
- SQLite runs in CI; it can move to a managed DB in Phase 9.

---

## 021 Rule-based document router (year → report Y and Y+1, no year → latest)

Date: 2026-11-16
Status: accepted

Context: With two 10-Ks in one collection, an unfiltered search drops quote recall
(near-duplicate distractors, see 020), but a fixed FY2025 filter cannot answer FY2024 questions.

Decision: Pick documents per question with backend/rag/router.py:
- An explicitly named report ("FY2024 10-K") → that report.
- A fiscal year Y → reports Y and Y+1 (the next 10-K repeats Y as a comparative year, possibly recast).
- No year → the latest report only.
- A year with no matching report → all reports.

Results (50q golden, 45 scored, docs_v2_2, body-only, k=5):
| route  | page | quote |
| none   | 0.79 | 0.69  |
| oracle | 0.88 | 0.77  |
| rule   | 0.86 | 0.76  |

Consequences:
- The rule router is within 0.01 of the oracle ceiling; the remaining gap is ranking, not routing.
- Only loss vs oracle: q33 ("fiscal 2024" opened the FY2024 report; its p39 crowded out FY2025 p35).
  To check: does the FY2024 report repeat that sentence? If so, add it as an alternative.
- q41 fails even with oracle routing. Two of its facts also appear in the FY2024 segment note
  (p82, one table chunk holding both rows); added as alternatives, the best rank moved 17 → 7,
  still outside k=5. Page recall credited p82 via a different chunk on the same page, a reminder
  that page-level hits can overstate retrieval (quote recall stays the primary metric).
  Remaining fix is multi-fact retrieval → Phase 7 query decomposition.
- Lesson: before blaming retrieval for a 0, check the golden set for the same fact elsewhere.
- Opening more documents costs top-k slots, not LLM tokens.

---

## 020. Adding a prior-year report hurts retrieval; metadata filters recover it
*2026-11-16 · Phase 6*

**Results** (40 questions, all about FY2025; body-only, k=5)

| setting | page recall@5 | quote recall@5 |
|---|---|---|
| FY2025 only (refactor check: identical to before 019) | 0.87 | 0.83 |
| FY2025 + FY2024, no filter | 0.76 | 0.73 |
| FY2025 + FY2024, filtered to FY2025 | 0.87 | 0.83 |

- The FY2024 10-K shares template and wording with the FY2025 10-K; its MD&A paragraphs
  took top-5 slots (q01, q27, q29, q33, q40 lost ranks). Near-duplicate documents from the
  same company are worse distractors than unrelated documents.
- A `doc_id` filter restores the single-document score exactly.

**Consequences**
- Choosing the document is required before retrieval. Users will not pick filters,
  so the router (Phase 7) must infer the target year/document from the question.
- 0.83 is the ceiling if the router always picks correctly; 0.73 is the floor with no routing.

---

## 019. Multiple documents: registry, document-prefixed IDs, one collection with metadata
*2026-11-16 · Phase 6*

**Context**
Code assumed one document: hard-coded name and exhibit page, paths without a document,
chunk IDs and citations by page only. The FY2024 10-K differs in structure
(102 pages; exhibits start at p89 with an insider trading policy absent from FY2025).

**Decision**
- `data/sources.json` is the document registry; `registry.py` derives labels and paths from it.
- Paths: `data/parsed/<doc_id>/<parser>/`, `data/chunks/<doc_id>/<parser>.jsonl`.
- Chunk IDs are prefixed with the document (`amat-10k-fy2025:c0123`); chunks carry
  `doc_id`, `doc_label`, `doc_type`, `fiscal_year`.
- One Milvus collection for all documents; documents are selected by metadata filter,
  not by separate collections.
- Citations show document and page (`FY2025 10-K p32`). Golden evidence carries `doc`;
  a quote counts only when found in a chunk of the same document.

**Consequences**
- The same year appears in several reports, sometimes recast (FY2024 Corporate and Other:
  $155M in the FY2024 10-K, $1,040M in the FY2025 10-K). Answers must name the source document.

---

## 018. CI runs lightweight checks only
*2026-11-15 · Phase 5*

**Decision**
GitHub Actions runs on every PR and push to main:
- `evals/validate.py evals/golden.jsonl` (structure only)
- `pytest tests/` (chunking rules, golden-set tools)

Not in CI: PDF quote checks (PDFs are not in the repo, 006), retrieval/answer/judge evals
(need models, Milvus, API keys, and cost money). These stay manual and are logged in
`evals/results*.md`.

**Consequences**
- `chunk.py` imports `sentence_transformers` lazily so tests do not pull in PyTorch.
- Tests encode incidents we actually hit: glued JSONL lines, dangling section-label rows.

---

## 017. Parser and chunking compared on the 40-question set: keep LlamaParse + v2.2
*2026-11-15 · Phase 5 · closes the open checks in 008 and 010*

**Context**
008 chose LlamaParse after a visual comparison of 4 pages; 010 adopted chunking v2 without
measuring it against v1. Both were to be checked with recall.

**Results** (body-only, k=5, bge-small, 36 answerable questions)

| parser | chunking | page recall@5 | quote recall@5 |
|---|---|---|---|
| LlamaParse | v2.2 | 0.87 | **0.83** |
| PyMuPDF | v2.2 | 0.88 | 0.74 |
| LlamaParse | v1 | 0.78 | 0.67 |

- Chunking mattered more than the parser: v1 → v2.2 +16 points; PyMuPDF → LlamaParse +9 points.
- LlamaParse won mostly on tables (q06, q22, q27, q40) and where headings separate topics (q23).
- PyMuPDF won q01's tax paragraph (rank 1 vs 20): without headings, cause and effect landed
  in one chunk. v1 won single-sentence facts (q18, q23) that v2.2's larger chunks dilute.
- Page recall ranked PyMuPDF first; quote recall did not. Page-level scoring is misleading.

**Decision**
Keep LlamaParse + chunking v2.2.

**Consequences**
- Golden quotes were validated on PyMuPDF text, which slightly favors PyMuPDF in matching;
  LlamaParse's real advantage may be larger.
- Next ideas: small-to-big retrieval (search small units, pass the parent chunk to the LLM),
  and keeping a cause paragraph with the table it explains.
- One document only; re-check when more documents are added (Phase 6).

---

## 016. Faithfulness judge: sentence-level LLM judge, validated by human labels and planted errors
*2026-11-15 · Phase 5*

**Context**
Format and evidence metrics cannot tell whether a sentence actually matches the chunks it cites.

**Decision**
- An LLM judge labels each answer sentence as supported / partially_supported / unsupported,
  seeing only the chunks that sentence cites. Inferred sentences pass only if the inference
  or calculation follows from the sources.
- Judge model is configurable (`JUDGE_MODEL`); currently the same as the answer model.
- The judge is validated two ways before its scores are used:
  1. agreement with 20 hand-labeled sentences
  2. 12 planted errors (wrong number, cause, direction, entity, unit, swapped facts,
     unsupported additions, wrong calculation, overclaim) plus 2 unchanged controls

**Results**
- Real answers (40 questions, 44 sentences): 95% supported, 5% partially supported, 0 unsupported.
- Human agreement: 19/20. The one disagreement (q25) was judge inconsistency:
  the same "end of FY2025 = October 26, 2025" wording was accepted in q20 and penalized in q25.
- Planted errors: 12/12 caught, controls 2/2 kept, exact labels 14/14.

**Consequences**
- Faithfulness ≠ completeness: q18 was faithful but did not give the actual cause.
- The judge cannot verify claims about the whole report ("the report does not state X");
  answers must say "the retrieved sections do not state X".
- The planted-error set is small, hand-written, and one error per sentence; self-preference
  (same model generating and judging) is not tested. Re-run with a different `JUDGE_MODEL`.

---

## 015. Unanswerable questions accept `partial` answers
*2026-11-14 · Phase 5*

q38 ("how many shares will be repurchased in fiscal 2026?") returned `partial`:
it stated that no share count is given and cited the $14.0B remaining authorization.
This is more useful than "no evidence", so unanswerable questions now pass with
`no_evidence` or `partial`, and fail only with `answered`.
A `partial` answer could still contain an invented figure; the faithfulness check (5-3)
must cover these answers.

---

## 014. Exclude exhibits from retrieval by default (supersedes part of 011)
*2026-11-14 · Phase 5*

**Context**
011 kept the body-only filter off because it had no effect on 9 questions.
On the 40-question set, q23 ("how many employees?") failed: three exhibit pages
(the subsidiary list, p88–90) took top-5 slots and pushed the answer (p9) to rank 7.

**Decision**
Filter retrieval to `section == "body"` by default, in both evaluation and answer generation.

**Results** (40 questions, 36 answerable)
- quote recall@5 0.77 → 0.80, page recall@5 0.84 → 0.87. Only q23 changed (rank 7 → 4); no question got worse.

**Consequences**
- Exhibits (subsidiaries, auditor consent, certifications) are unreachable by default.
  Questions that need them must be routed explicitly (Phase 7 router).
- 9 questions were too few to see this effect; settings judged on small sets should be re-checked.

---

## 013. Golden evidence may list alternative locations
*2026-11-12 · Phase 5*

**Context**
q03 was answered correctly from the EPS note (p60), but the golden set listed the diluted
share counts only on p47, so evidence hit scored 0/2. Reporting which chunk actually matched
revealed more repeats: the tax remeasurement sentence appears in MD&A (p32) and the income tax
note (p77); the workforce reduction appears in MD&A (p31) and the restructuring note (p70).

**Decision**
- An evidence item may list `alternatives` (page + quote). Any one location counts as found.
- `validate.py --pdf` checks every alternative quote against the PDF.
- Retrieval output shows the page of the chunk that actually matched.

**Results**
- Without changing the system: retrieval quote recall@5 0.69 → 0.75, answer evidence hit 0.64 → 0.69.
- Page recall is now computed per evidence item (was per unique page); earlier page recall
  values are not comparable.
- q03's buyback evidence still ranks 68+ even with a sentence-form alternative (p35):
  the question never mentions buybacks (vocabulary gap, see 011).

**Consequences**
- New golden questions must check for repeated facts across MD&A, statements, and notes.

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
**Status:** body-only part superseded by 014

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
**Status:** confirmed by 017 (v1 vs v2.2: quote recall 0.67 → 0.83)

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
**Status:** confirmed by 017 (40-question recall comparison)

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