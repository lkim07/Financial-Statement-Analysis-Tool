"""일회성 스크립트: q53, q57의 Summary Compensation Table 대안 quote를 청크 표기에 맞춘다.

처음 쓴 "29,649,352 27,799,212"는 PDF 텍스트(열 순서)에서는 붙어 있지만,
LlamaParse 청크에서는 2025 행과 2024 행으로 나뉘어 있어서 평가가 영원히 찾지 못한다.
8자리 금액은 문서 안에서 하나의 사실만 가리키므로 숫자만 quote로 쓴다.
실행 후 validate.py --check-quotes --check-chunks로 확인하고 이 파일은 지운다.
"""
import json
from pathlib import Path

GOLDEN = Path("evals/golden.jsonl")
P = "amat-def14a-2026"
SCT = "Executive Compensation > Summary Compensation Table"

rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
by_id = {r["id"]: r for r in rows}

q53 = by_id["q53"]["evidence"]
q53[0]["alternatives"] = [{"doc": P, "page": 71, "section": SCT, "quote": "29,649,352"}]
q53[1]["alternatives"] = [{"doc": P, "page": 71, "section": SCT, "quote": "27,799,212"}]

q57 = by_id["q57"]["evidence"][0]
if not any(a["quote"] == "29,649,352" for a in q57.get("alternatives", [])):
    q57.setdefault("alternatives", []).append({"doc": P, "page": 71, "section": SCT, "quote": "29,649,352"})

GOLDEN.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
print("patched q53, q57")