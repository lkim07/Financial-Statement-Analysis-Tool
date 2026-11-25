// 질문 -> 답변(왼쪽) -> 출처를 누르면 PDF에 하이라이트(오른쪽).
import { useState } from "react";
import { AnswerPanel } from "./AnswerPanel";
import { ask, type AskResponse } from "./api";
import { PdfViewer } from "./PdfViewer";
import "./App.css";

export default function App() {
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onAsk = async (question: string) => {
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

  const source = answer && selected ? (answer.sources[selected] ?? null) : null;

  return (
    <div className="app">
      <h1>Evidence-Based Financial Report Analyst</h1>
      <main>
        <AnswerPanel
          loading={loading}
          error={error}
          answer={answer}
          selected={selected}
          onAsk={onAsk}
          onSelect={setSelected}
        />
        <PdfViewer source={source} />
      </main>
    </div>
  );
}
