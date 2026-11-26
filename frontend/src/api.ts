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

// ---------- 대시보드 (GET /reports/{doc_id}/summary) ----------

export type Kpi = {
  item: string;
  name: string;
  kind: "usd" | "pct" | "per_share";
  current: number;
  prior: number;
  change: number;
  change_pct?: number | null;
  computed: boolean; // 손익계산서에 없는 값(매출총이익률)을 코드가 계산했나
  source: Source;
};

export type Bar = {
  item: string;
  label: string;
  kind: "total" | "up" | "down";
  start: number; // 막대 아래(또는 위) 끝: 앞 막대가 끝난 높이
  end: number;
  value: number; // total이면 순이익, 아니면 기여도
  question?: string; // "Why?" 버튼이 Ask 탭에 넘길 질문
  source: Source;
};

export type Summary = {
  doc_id: string;
  label: string;
  year: number;
  kpis: Kpi[];
  waterfall: { year: number; prior_year: number; change: number; bars: Bar[] };
};

export type DocInfo = { doc_id: string; label: string; doc_type: string; fiscal_year: number };

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  return res.json();
}

export const listDocs = () => getJson<DocInfo[]>("/docs/list");
export const getSummary = (docId: string) => getJson<Summary>(`/reports/${docId}/summary`);

/** 숫자 표기: 백만 달러는 쉼표, EPS는 소수 둘째 자리, 비율은 %. 음수 부호는 통화 기호 앞에. */
export function formatValue(value: number, kind: Kpi["kind"] | "usd"): string {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (kind === "pct") return `${sign}${abs.toFixed(1)}%`;
  if (kind === "per_share") return `${sign}$${abs.toFixed(2)}`;
  return `${sign}$${Math.round(abs).toLocaleString()}M`;
}