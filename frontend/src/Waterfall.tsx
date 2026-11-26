// 순이익 워터폴을 SVG로 직접 그린다.
// 막대마다 서버가 계산한 start/end(누적 높이)를 그대로 쓴다. 이 컴포넌트는 "값 -> 화면 좌표"만 한다.
import type { Bar } from "./api";

const W = 720; // viewBox 폭. 실제 화면 폭은 CSS(width: 100%)가 정하고, SVG가 알아서 늘이고 줄인다
const H = 300;
const PAD = { top: 24, right: 12, bottom: 70, left: 56 };
const COLORS = { total: "#888780", up: "#2a78d6", down: "#e34948" };

type Props = { bars: Bar[]; selected: number | null; onSelect: (index: number) => void };

export function Waterfall({ bars, selected, onSelect }: Props) {
  // 세로축 범위: 0부터 그리면 변화가 안 보인다. 가장 낮은 계단보다 조금 아래에서 자른다 (잘렸다고 표시한다)
  const lows = bars.flatMap((b) => (b.kind === "total" ? [b.end] : [b.start, b.end]));
  const highs = bars.flatMap((b) => [b.start, b.end]);
  const span = Math.max(...highs) - Math.min(...lows);
  const step = niceStep(span / 4);
  const yMin = Math.max(0, Math.floor((Math.min(...lows) - span * 0.25) / step) * step);
  const yMax = Math.ceil((Math.max(...highs) + span * 0.1) / step) * step;

  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const y = (v: number) => PAD.top + ((yMax - Math.max(v, yMin)) / (yMax - yMin)) * plotH;
  const slot = plotW / bars.length;
  const barW = Math.min(slot * 0.6, 48);

  const ticks: number[] = [];
  for (let t = yMin; t <= yMax; t += step) ticks.push(t);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="waterfall" role="img"
         aria-label="Waterfall from prior-year to current-year net income, one bar per income statement driver">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
          <text x={PAD.left - 6} y={y(t) + 4} className="tick" textAnchor="end">{t.toLocaleString()}</text>
        </g>
      ))}
      {yMin > 0 && (
        // 축이 0에서 시작하지 않는다는 표시: 처음과 끝 막대는 실제로 0부터 서 있다
        <text x={PAD.left} y={H - PAD.bottom + 52} className="axis-note">
          Axis starts at {yMin.toLocaleString()} (USD millions); total bars rise from 0.
        </text>
      )}

      {bars.map((b, i) => {
        const x = PAD.left + i * slot + (slot - barW) / 2;
        const top = b.kind === "total" ? y(b.end) : y(Math.max(b.start, b.end));
        const bottom = b.kind === "total" ? y(yMin) : y(Math.min(b.start, b.end));
        const label = b.kind === "total" ? b.value.toLocaleString() : `${b.value > 0 ? "+" : "-"}${Math.abs(b.value).toLocaleString()}`;
        const next = bars[i + 1];
        return (
          <g key={i} className={`bar${selected === i ? " selected" : ""}`} onClick={() => onSelect(i)}>
            <rect x={x} y={top} width={barW} height={Math.max(bottom - top, 1)} rx={3} fill={COLORS[b.kind]} />
            <text x={x + barW / 2} y={top - 6} className="value" textAnchor="middle">{label}</text>
            <text x={x + barW / 2} y={H - PAD.bottom + 14} className="tick"
                  textAnchor="end" transform={`rotate(-35 ${x + barW / 2} ${H - PAD.bottom + 14})`}>
              {short(b.label)}
            </text>
            {next && next.kind !== "total" && (
              // 계단을 잇는 선: 이 막대가 끝난 높이 = 다음 막대가 시작하는 높이
              <line x1={x + barW} x2={x + slot} y1={y(b.end)} y2={y(b.end)} className="connector" />
            )}
          </g>
        );
      })}
    </svg>
  );
}

function niceStep(raw: number): number {
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? 10 * p;
}

function short(label: string): string {
  return label
    .replace("Provision for income taxes", "Income taxes")
    .replace("Interest and other income (expense), net", "Other income")
    .replace("Research, development and engineering", "RD&E")
    .replace("General and administrative", "G&A")
    .replace("Marketing and selling", "Marketing");
}