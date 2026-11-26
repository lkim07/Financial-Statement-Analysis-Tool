"""테스트가 backend/ingest와 evals의 모듈을 import할 수 있게 경로를 추가한다."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend" / "ingest"), str(ROOT / "backend" / "rag"), str(ROOT / "backend" / "numbers"), str(ROOT / "backend" / "api"), str(ROOT / "evals")]