// 두 탭이 오른쪽 PDF 뷰어 하나를 같이 쓴다.
// - Dashboard: 지표 카드와 워터폴. 누르면 숫자가 있는 행을 칠한다.
// - Ask: 질문 -> 답변. 문장이나 출처를 누르면 근거를 칠한다.
// 두 탭은 숨기기만 하고 없애지 않는다: 탭을 오가도 고른 막대, 질문, 답변이 그대로 남는다.
// 가운데 경계선은 끌어서(또는 키보드 ←/→) 두 칸의 폭을 바꾼다. 마지막 폭은 브라우저에 기억해 둔다.
import { useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { AnswerPanel } from "./AnswerPanel";
import { ask, type AskResponse, type Source } from "./api";
import { Dashboard } from "./Dashboard";
import { PdfViewer } from "./PdfViewer";
import "./App.css";

type Tab = "dashboard" | "ask";
const SPLIT_KEY = "split-left-pct";
const clamp = (v: number) => Math.min(Math.max(v, 20), 80);

function savedSplit(): number {
  try {
    const v = Number(localStorage.getItem(SPLIT_KEY));
    return v ? clamp(v) : 42;
  } catch {
    return 42; // 저장소를 못 쓰는 환경에서도 화면은 뜨게
  }
}

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null); // Ask 탭에서 고른 출처 id
  const [dashSource, setDashSource] = useState<Source | null>(null); // Dashboard에서 고른 출처
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [leftPct, setLeftPct] = useState(savedSplit);
  const mainRef = useRef<HTMLElement>(null);
  const dragging = useRef(false);

  const onAsk = async (question: string) => {
    setTab("ask");
    setLoading(true);
    setError(null);
    try {
      const result = await ask(question);
      setAnswer(result);
      // 첫 문장의 첫 출처를 바로 연다: 질문하자마자 근거가 보이게
      setSelected(result.sentences.find((s) => s.citations.length)?.citations[0] ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const saveSplit = (pct: number) => {
    try {
      localStorage.setItem(SPLIT_KEY, String(pct));
    } catch {
      /* 기억하지 못해도 괜찮다 */
    }
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    if (!dragging.current || !mainRef.current) return;
    const rect = mainRef.current.getBoundingClientRect();
    setLeftPct(clamp(((e.clientX - rect.left) / rect.width) * 100));
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
    saveSplit(leftPct);
  };

  const askSource = answer && selected ? (answer.sources[selected] ?? null) : null;
  const source = tab === "ask" ? askSource : dashSource;

  return (
    <div className="app">
      <header className="top">
        <h1>Evidence-Based Financial Report Analyst</h1>
        <nav>
          <button type="button" className={tab === "dashboard" ? "active" : ""} onClick={() => setTab("dashboard")}>Dashboard</button>
          <button type="button" className={tab === "ask" ? "active" : ""} onClick={() => setTab("ask")}>Ask</button>
        </nav>
      </header>
      <main ref={mainRef} style={{ gridTemplateColumns: `${leftPct}% 8px 1fr` }}>
        <div className="left">
          <div hidden={tab !== "dashboard"} className="panel">
            <Dashboard onShow={setDashSource} onWhy={onAsk} />
          </div>
          <div hidden={tab !== "ask"} className="panel">
            <AnswerPanel loading={loading} error={error} answer={answer} selected={selected}
                         onAsk={onAsk} onSelect={setSelected} />
          </div>
        </div>
        <div
          className="divider"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panels"
          aria-valuenow={Math.round(leftPct)}
          tabIndex={0}
          onPointerDown={(e) => {
            dragging.current = true;
            e.currentTarget.setPointerCapture(e.pointerId); // 마우스가 선 밖으로 나가도 계속 끌린다
          }}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onKeyDown={(e) => {
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            const next = clamp(leftPct + (e.key === "ArrowLeft" ? -2 : 2));
            setLeftPct(next);
            saveSplit(next);
          }}
        />
        <PdfViewer source={source} />
      </main>
    </div>
  );
}