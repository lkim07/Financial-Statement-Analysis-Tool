// 왼쪽: 질문 입력, 예시 질문, 답변 문장과 출처 칩.
import { useState } from "react";
import type { AskResponse, Source } from "./api";

const EXAMPLES = [
  "Revenue and operating income both increased in FY2025. Why did net income decrease?",
  "Net income decreased, so why did diluted EPS increase?",
  "What was the CEO's total compensation for fiscal 2025?",
];

const STATUS_TEXT = {
  answered: "Answered",
  partial: "Partly answered",
  no_evidence: "No evidence found",
} as const;

type Props = {
  loading: boolean;
  error: string | null;
  answer: AskResponse | null;
  selected: string | null;
  onAsk: (question: string) => void;
  onSelect: (sourceId: string) => void;
};

function chipText(source: Source): string {
  return source.kind === "computed" ? `Calculated: ${source.heading}` : source.label;
}

export function AnswerPanel({ loading, error, answer, selected, onAsk, onSelect }: Props) {
  const [question, setQuestion] = useState(EXAMPLES[0]);

  const submit = (q: string) => {
    setQuestion(q);
    if (q.trim().length >= 3) onAsk(q.trim());
  };

  return (
    <section className="answer-panel">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(question);
        }}
      >
        <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} />
        <button type="submit" disabled={loading}>
          {loading ? "Thinking…" : "Ask"}
        </button>
      </form>

      <div className="examples">
        {EXAMPLES.map((q) => (
          <button key={q} type="button" onClick={() => submit(q)} disabled={loading}>
            {q}
          </button>
        ))}
      </div>

      {loading && <p className="hint">Routing, searching the reports and checking every citation…</p>}
      {error && <p className="error">{error}</p>}

      {answer && !loading && (
        <div className="answer">
          <p className="asked">Q: {answer.question}</p>
          <p className={`status status-${answer.status}`}>{STATUS_TEXT[answer.status]}</p>
          <ol>
            {answer.sentences.map((s, i) => (
              <li
                key={i}
                className={s.inferred ? "sentence inferred" : "sentence"}
                // 문장을 누르면 그 문장의 첫 출처를 연다
                onClick={() => s.citations[0] && onSelect(s.citations[0])}
              >
                <span>{s.text}</span>
                {s.inferred && <span className="tag">inferred</span>}
                <div className="chips">
                  {s.citations.map((cid) => {
                    const src = answer.sources[cid];
                    if (!src) return null;
                    return (
                      <button
                        key={cid}
                        type="button"
                        className={`chip chip-${src.kind}${cid === selected ? " active" : ""}`}
                        onClick={(e) => {
                          e.stopPropagation(); // 문장 클릭과 겹치지 않게
                          onSelect(cid);
                        }}
                      >
                        {chipText(src)}
                      </button>
                    );
                  })}
                </div>
              </li>
            ))}
          </ol>
          {answer.missing && <p className="missing">Not found in the retrieved sections: {answer.missing}</p>}
          {answer.route.decomposed && (
            <p className="hint">Explained with a bridge: {answer.route.drivers.join(", ")}</p>
          )}
          {answer.problems.length > 0 && <p className="error">Checks: {answer.problems.join("; ")}</p>}
        </div>
      )}
    </section>
  );
}