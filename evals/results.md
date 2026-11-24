# Retrieval results

| date | collection | k | prefix | body only | page recall | quote recall | n | note |
|---|---|---|---|---|---|---|---|---|
| 2026-11-11 | chunks_llamaparse_v2_2 | 5 | True | True | 0.70 | 0.67 | 9 | bge query prefix |
| 2026-11-11 | chunks_llamaparse_v2_2 | 5 | True | True | 0.74 | 0.69 | 9 | bge query prefix |
| 2026-11-11 | chunks_llamaparse_v2_2 | 5 | False | False | 0.74 | 0.69 | 9 | v2.2 baseline |
| 2026-11-11 | chunks_llamaparse_v2_2 | 5 | True | False | 0.74 | 0.69 | 9 | bge prefix |
| 2026-11-11 | chunks_llamaparse_v2_2 | 5 | False | True | 0.74 | 0.69 | 9 | body only |
| 2026-11-11 | chunks_llamaparse_v2_2 | 5 | True | True | 0.74 | 0.69 | 9 | prefix + body only |
| 2026-11-11 | chunks_llamaparse_v2_2 | 20 | False | False | 0.74 | 0.75 | 9 | k=20 ceiling |
| 2026-11-12 | chunks_llamaparse_v2_2 | 5 | False | False | 0.81 | 0.75 | 9 | with alternatives |
| 2026-11-12 | chunks_llamaparse_v2_2 | 5 | False | False | 0.81 | 0.75 | 9 | with alternatives |
| 2026-11-14 | chunks_llamaparse_v2_2 | 5 | False | False | 0.81 | 0.75 | 9 | 40 questions |
| 2026-11-14 | chunks_llamaparse_v2_2 | 5 | False | False | 0.84 | 0.77 | 35 | 40 questions |
| 2026-11-14 | chunks_llamaparse_v2_2 | 5 | False | True | 0.87 | 0.80 | 35 | 40q body only |
| 2026-11-15 | chunks_llamaparse_v2_2 | 5 | False | True | 0.87 | 0.83 | 35 | 40q body only + norm fix |
| 2026-11-15 | chunks_pymupdf_v2_2 | 5 | False | True | 0.88 | 0.74 | 35 | pymupdf v2.2 |
| 2026-11-15 | chunks_llamaparse_v1 | 5 | False | True | 0.78 | 0.67 | 35 | llamaparse v1 |
| 2026-11-16 | docs_fy2025 | 5 | False | True | 0.87 | 0.83 | 35 | fy2025 only (refactor check) |
| 2026-11-16 | docs_v2_2 | 5 | False | True | 0.76 | 0.73 | 35 | fy2025 + fy2024 |
| 2026-11-16 | docs_v2_2 | 5 | False | True | 0.87 | 0.83 | 35 | both indexed, filtered to fy2025 [docs: amat-10k-fy2025] |
| 2026-11-16 | docs_v2_2 | 5 | False | True | 0.79 | 0.69 | 45 | 50q no routing [route: none] |
| 2026-11-16 | docs_v2_2 | 5 | False | True | 0.88 | 0.77 | 45 | 50q oracle routing [route: oracle] |
| 2026-11-16 | docs_v2_2 | 5 | False | True | 0.86 | 0.76 | 45 | 50q rule router [route: rule] |
| 2026-11-16 | docs_v2_2 | 5 | False | True | 0.86 | 0.76 | 45 | 50q rule router, q41 alternatives [route: rule] |
| 2026-11-16 | docs_v2_2 | 5 | False | True | 0.86 | 0.76 | 45 | 50q rule router, q41 alternatives [route: rule] |
| 2026-11-22 | docs_v2_2 | 10 | False | True | 0.92 | 0.86 | 45 | 50q rule k10 baseline [route: rule] |
| 2026-11-22 | docs_v2_2 | 10 | False | True | 0.92 | 0.86 | 45 | bridge decomposition [route: rule] [decompose cov 0.8 dk 3] |
| 2026-11-22 | docs_v2_2 | 10 | False | True | 0.92 | 0.86 | 45 | 50q rule k10 baseline, p7 alternatives [route: rule] |
| 2026-11-22 | docs_v2_2 | 10 | False | True | 0.92 | 0.89 | 45 | decompose v2: statement seat + keep 4 [route: rule] [decompose cov 0.8 dk 3 keep 4] |
| 2026-11-23 | docs_v2_2 | 10 | False | True | 0.93 | 0.88 | 45 | decompose + EPS bridge [route: rule] [decompose cov 0.8 dk 3 keep 4] |
| 2026-11-23 | docs_v2_2 | 10 | False | True | 0.93 | 0.90 | 45 | decompose why-only + EPS bridge [route: rule] [decompose cov 0.8 dk 3 keep 4] |
| 2026-11-24 | docs_v2_3 | 10 | False | True | 0.91 | 0.86 | 52 | 3 docs, two-step router [route: rule] [decompose cov 0.8 dk 3 keep 4] |
| 2026-11-24 | docs_v2_3 | 10 | False | True | 0.92 | 0.88 | 52 | 3 docs, q53/q57 quotes fixed (golden fix, not system) [route: rule] [decompose cov 0.8 dk 3 keep 4] |

### 2026-11-24: Proxy (DEF 14A) added, two-step router (docs_v2_3)

- Collection `docs_v2_3`: FY2025 10-K + FY2024 10-K + 2026 proxy (1,008 chunks).
  `docs_v2_2` is kept for comparison.
- The original 45 questions score exactly as before (line by line).
  The router never opens the proxy for them.
- Proxy questions q51–q57: 4/7 → 5.5/7.
- **The 0.86 → 0.88 change is a golden-set fix, not a system change.**
  The q53/q57 alternative quotes were written from PDF text (column order) and could never match
  LlamaParse chunks (row order). Added `validate.py --check-chunks` so a quote must exist
  in a chunk under the same normalization the evaluator uses.
- Remaining failures:
  - q54: router miss. "fees paid to KPMG" has no proxy keyword, so it went to the 10-K.
  - q57: cross-document; the net income evidence is ranked 32nd.
  - q44: ranking (15th and 13th). Its quotes do exist in the chunks.
| 2026-11-24 | docs_v2_3 | 10 | False | True | 0.94 | 0.90 | 52 | cascade router [route: cascade] [decompose cov 0.8 dk 3 keep 4] |
