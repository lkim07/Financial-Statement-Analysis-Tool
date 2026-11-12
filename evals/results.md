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
