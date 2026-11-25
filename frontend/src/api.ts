// FastAPI(/ask, /docs/...)와 주고받는 데이터의 모양. backend/api/service.py의 ask()와 짝이다.

export const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export type Box = [number, number, number, number]; // [x0, y0, x1, y1], PDF 포인트, 왼쪽 위가 원점

export type Highlight = {
  page: number;
  bbox: Box;
  level: "block" | "exact"; // block: 청크 전체(연하게), exact: 숫자가 있는 행(진하게)
};

export type Source = {
  kind: "chunk" | "computed";
  doc_id: string;
  label: string; // "FY2025 10-K p47" 또는 "COMPUTED from FY2025 10-K"
  page: number;
  heading: string;
  text?: string; // computed일 때만: 코드가 계산한 브리지 표
  highlights: Highlight[];
};

export type Sentence = {
  text: string;
  inferred: boolean; // 리포트가 말한 게 아니라 모델이 계산/추론한 문장
  citations: string[];
};

export type AskResponse = {
  question: string;
  status: "answered" | "partial" | "no_evidence";
  missing: string;
  sentences: Sentence[];
  sources: Record<string, Source>;
  route: { docs: string[]; decomposed: boolean; drivers: string[] };
  problems: string[];
};

export async function ask(question: string): Promise<AskResponse> {
  const res = await fetch(`${API_URL}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return res.json();
}

export const pdfUrl = (docId: string) => `${API_URL}/docs/${docId}/pdf`;

/** 출처를 열 때 처음 보여 줄 페이지와 높이: 진한 하이라이트가 있으면 그 첫 번째, 없으면 첫 블록. */
export function firstTarget(source: Source): { page: number; y: number } {
  const exact = source.highlights.find((h) => h.level === "exact");
  const first = exact ?? source.highlights[0];
  return first ? { page: first.page, y: first.bbox[1] } : { page: source.page, y: 0 };
}