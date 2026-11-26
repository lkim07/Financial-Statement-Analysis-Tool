// 대시보드: 질문하지 않아도 보이는 것. 핵심 지표 카드와 순이익 워터폴.
// 카드나 막대를 누르면 오른쪽 PDF에서 그 숫자가 있는 손익계산서 행을 칠한다.
//
// 문서를 바꿔도 고른 항목은 유지한다: FY2025의 "Income taxes"를 보다가 FY2024로 바꾸면
// FY2024의 같은 막대가 선택되고 그 행이 칠해진다 (같은 항목을 두 해에 걸쳐 비교).
// 받아 온 요약은 문서마다 저장해 두고 다시 부르지 않는다.
import { useEffect, useState } from "react";
import type { Bar, DocInfo, Source, Summary } from "./api";
import { formatValue, getSummary, listDocs } from "./api";
import { Waterfall } from "./Waterfall";

type Props = { onShow: (source: Source) => void; onWhy: (question: string) => void };

/** 막대를 문서와 상관없이 가리키는 열쇠. 처음/끝 순이익은 item이 같아서 위치로 구분한다. */
function barKey(bar: Bar, index: number, bars: Bar[]): string {
  if (bar.kind !== "total") return bar.item;
  return index === 0 ? "start" : index === bars.length - 1 ? "end" : bar.item;
}

export function Dashboard({ onShow, onWhy }: Props) {
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [docId, setDocId] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Record<string, Summary>>({});
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listDocs()
      .then((all) => {
        const reports = all.filter((d) => d.doc_type === "10-K").sort((a, b) => b.fiscal_year - a.fiscal_year);
        setDocs(reports);
        setDocId((cur) => cur ?? reports[0]?.doc_id ?? null); // 최신 리포트부터
      })
      .catch((e) => setError(String(e)));
  }, []);

  /** 이 요약에서 고른 항목의 막대를 PDF에 보여 준다. 이벤트와 응답 처리에서만 부른다
      (렌더링 중에 부모의 상태를 바꾸면 React가 경고한다). */
  const showSelected = (s: Summary, key: string | null) => {
    const bars = s.waterfall.bars;
    const bar = bars.find((b, i) => barKey(b, i, bars) === key);
    if (bar) onShow(bar.source);
  };

  useEffect(() => {
    if (!docId || summaries[docId]) return; // 이미 받아 둔 문서는 다시 부르지 않는다
    let cancelled = false; // 문서를 빨리 바꾸면 늦게 온 옛 응답이 새 화면을 덮지 않게
    getSummary(docId)
      .then((s) => {
        if (cancelled) return;
        setSummaries((prev) => ({ ...prev, [s.doc_id]: s }));
        showSelected(s, selectedKey); // 처음 받는 문서도 고른 항목을 바로 보여 준다
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
    // showSelected/selectedKey가 바뀔 때마다 다시 부르면 안 된다: 문서가 바뀔 때만 받아 온다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, summaries]);

  const summary = docId ? summaries[docId] : undefined;
  const bars = summary?.waterfall.bars ?? [];
  const selectedIndex = bars.findIndex((b, i) => barKey(b, i, bars) === selectedKey);
  const bar = selectedIndex >= 0 ? bars[selectedIndex] : null;

  if (error) return <section className="dashboard"><p className="error">{error}</p></section>;
  if (!summary) return <section className="dashboard"><p className="hint">Loading the financial statements…</p></section>;

  const wf = summary.waterfall;

  return (
    <section className="dashboard">
      <div className="dash-head">
        <select
          value={docId ?? ""}
          onChange={(e) => {
            setDocId(e.target.value);
            const cached = summaries[e.target.value];
            if (cached) showSelected(cached, selectedKey); // 받아 둔 문서면 같은 항목을 바로 보여 준다
          }}
        >
          {docs.map((d) => <option key={d.doc_id} value={d.doc_id}>{d.label}</option>)}
        </select>
        <span className="hint">Fiscal {wf.prior_year} → {wf.year}. Numbers come from the statements, computed by code.</span>
      </div>

      <div className="kpis">
        {summary.kpis.map((k) => (
          <button key={k.item} type="button" className="kpi" onClick={() => onShow(k.source)}>
            <span className="kpi-name">{k.name}{k.computed && " (calculated)"}</span>
            <span className="kpi-value">{formatValue(k.current, k.kind)}</span>
            <span className={`kpi-change ${k.change >= 0 ? "up" : "down"}`}>
              {k.kind === "pct"
                ? `${k.change >= 0 ? "+" : ""}${k.change.toFixed(1)} pts`
                : `${k.change >= 0 ? "+" : ""}${formatValue(k.change, k.kind)}${k.change_pct != null ? ` (${k.change_pct >= 0 ? "+" : ""}${k.change_pct}%)` : ""}`}
            </span>
          </button>
        ))}
      </div>

      <h2>Why net income changed: {formatValue(wf.change, "usd")}</h2>
      <Waterfall bars={bars} selected={selectedIndex >= 0 ? selectedIndex : null}
                 onSelect={(i) => {
                   setSelectedKey(barKey(bars[i], i, bars));
                   onShow(bars[i].source);
                 }} />
      <p className="legend">
        <span className="sw total" /> net income <span className="sw up" /> raised net income <span className="sw down" /> lowered net income
      </p>

      {bar && (
        <div className="bar-detail">
          <strong>{bar.label}</strong>{" "}
          {bar.kind === "total"
            ? formatValue(bar.value, "usd")
            : `${bar.value > 0 ? "+" : ""}${formatValue(bar.value, "usd")} to net income`}
          {bar.question && <button type="button" onClick={() => onWhy(bar.question!)}>Why? Ask the reports ↗</button>}
        </div>
      )}
      {selectedKey && selectedIndex < 0 && (
        <p className="hint">This item did not change net income in {summary.label}, so it has no bar here.</p>
      )}
    </section>
  );
}