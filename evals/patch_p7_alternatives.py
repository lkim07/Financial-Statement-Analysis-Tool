"""일회성 스크립트: q01, q42의 숫자 근거에 손익계산서 위치를 alternatives로 추가한다.

같은 숫자가 MD&A 표(p30, p34, p36)와 손익계산서(FY2025 p47, FY2024 p52)에 둘 다 있다 (decision 013).
이미 같은 alternative가 있으면 건너뛴다. 실행 후 validate.py --check-quotes로 확인하고 이 파일은 지운다.
"""
import json
from pathlib import Path

GOLDEN = Path("evals/golden.jsonl")
IS = "Consolidated Statements of Operations"

# (질문 id, 원래 quote) -> 추가할 alternative
PATCHES = {
    ("q01", "Net income $ 6,998 $ 7,177 $ (179)"):
        {"doc": "amat-10k-fy2025", "page": 47, "section": IS, "quote": "Net income $ 6,998 $ 7,177"},
    ("q42", "Net income $ 7,177 $ 6,856 $ 321"):
        {"doc": "amat-10k-fy2024", "page": 52, "section": IS, "quote": "Net income $ 7,177 $ 6,856"},
    ("q42", "Interest and other income (expense), net $ 532 $ 300 $ 232"):
        {"doc": "amat-10k-fy2024", "page": 52, "section": IS, "quote": "Interest and other income (expense), net 532 300"},
    ("q42", "Provision for income taxes $ 975 $ 860 $ 115"):
        {"doc": "amat-10k-fy2024", "page": 52, "section": IS, "quote": "Provision for income taxes 975 860"},
}

rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
added = 0
for row in rows:
    for ev in row["evidence"]:
        alt = PATCHES.get((row["id"], ev["quote"]))
        if alt and alt not in ev.get("alternatives", []):
            ev.setdefault("alternatives", []).append(alt)
            added += 1
GOLDEN.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
print(f"added {added} alternatives (expected {len(PATCHES)})")