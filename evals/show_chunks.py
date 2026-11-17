"""한 문서의 특정 페이지에 어떤 청크가 있는지 그대로 보여준다.

golden.jsonl에 quote/alternatives를 넣기 전에, 청크 텍스트에 실제로 어떻게 적혀 있는지
(표 행이 '|'로 나뉘었는지, '$'가 붙었는지 등) 확인하는 용도.

사용법:
    python evals/show_chunks.py --doc amat-10k-fy2024 --page 82
    python evals/show_chunks.py --doc amat-10k-fy2024 --page 82 --grep Display
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--parser", default="llamaparse")
    parser.add_argument("--grep", help="only chunks containing this text")
    args = parser.parse_args()

    path = Path("data/chunks") / args.doc / f"{args.parser}.jsonl"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        chunk = json.loads(line)
        if chunk["page"] != args.page:
            continue
        if args.grep and args.grep not in chunk["text"]:
            continue
        print(f"=== {chunk['chunk_id']}  [{chunk['type']}]  {chunk.get('heading', '')}")
        print(chunk["text"])
        print()


if __name__ == "__main__":
    main()