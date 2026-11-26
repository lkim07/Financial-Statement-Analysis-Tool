// 오른쪽: PDF 한 페이지 + 하이라이트 사각형.
//
// 좌표 변환: 화면 px = PDF 포인트 × 배율, 배율 = 화면에 그린 폭(px) / 페이지 원래 폭(pt).
// - 화면 폭: 이 칸의 폭(ResizeObserver) × 확대 비율(zoom)
// - 페이지 원래 폭: API에는 없다. react-pdf가 페이지를 읽을 때 알려 준다 (onLoadSuccess의 originalWidth)
// 좌표는 PyMuPDF 기준(왼쪽 위 원점)이라 y를 뒤집지 않는다.
//
// 깜빡임 없애기 (Phase 8-4 사용자 피드백):
// 1) 이중 버퍼: 새 페이지/폭은 보이지 않는 곳에서 그리고, 다 그려지면(onRenderSuccess) 그때 바꿔 끼운다.
//    그동안은 이전 페이지가 그대로 보인다. 같은 페이지 안에서 하이라이트만 바뀌면 다시 그리지 않는다.
// 2) 폭 흔들림: 세로 스크롤바가 생겼다 없어지며 폭이 바뀌고 -> 다시 그리고 -> 스크롤바가 바뀌는 고리.
//    스크롤바 자리를 항상 비워 두고(scrollbar-gutter), 폭은 8px 단위로, 크기 조절이 멈춘 뒤에만 반영한다.
import { useCallback, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import type { Highlight, Source } from "./api";
import { firstTarget, pdfUrl } from "./api";

// worker 설정은 react-pdf를 쓰는 바로 이 파일에 둬야 한다 (react-pdf README)
pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

const ZOOMS = [0.75, 1, 1.25, 1.5, 2];

type Rendered = { docId: string; page: number; width: number; pageWidthPt: number };

export function PdfViewer({ source }: { source: Source | null }) {
  const [box, setBox] = useState<HTMLDivElement | null>(null); // 스크롤 칸 (callback ref: 요소가 바뀌어도 다시 잰다)
  const [fitWidth, setFitWidth] = useState(800);
  const [zoom, setZoom] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [shown, setShown] = useState<Rendered | null>(null); // 지금 화면에 보이는(다 그려진) 페이지
  const pendingWidthPt = useRef<number | null>(null);

  // 칸의 폭: 크기 조절이 멈춘 뒤(150ms)에만, 8px 단위로
  useEffect(() => {
    if (!box) return;
    let timer: number | undefined;
    const observer = new ResizeObserver(([entry]) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const w = Math.round(Math.min(entry.contentRect.width - 24, 1000) / 8) * 8;
        setFitWidth((prev) => (Math.abs(prev - w) >= 8 ? w : prev));
      }, 150);
    });
    observer.observe(box);
    return () => {
      window.clearTimeout(timer);
      observer.disconnect();
    };
  }, [box]);

  // 출처가 바뀌면 그 출처의 첫 하이라이트 페이지로 (렌더링 중에 이전 출처와 비교: React 문서 권장 패턴)
  const [shownSource, setShownSource] = useState<Source | null>(null);
  if (source !== shownSource) {
    setShownSource(source);
    if (source) setPage(firstTarget(source).page);
  }

  const width = Math.round(fitWidth * zoom);
  const docId = source?.doc_id ?? null;
  const ready = shown !== null && shown.docId === docId;
  const needsRender = docId !== null && !(ready && shown.page === page && shown.width === width);

  const onRendered = useCallback(() => {
    if (docId && pendingWidthPt.current) {
      setShown({ docId, page, width, pageWidthPt: pendingWidthPt.current });
    }
  }, [docId, page, width]);

  // 바꿔 끼운 뒤, 첫 하이라이트 높이로 스크롤
  useEffect(() => {
    if (!source || !shown || !box || shown.docId !== source.doc_id) return;
    const target = firstTarget(source);
    if (target.page === shown.page) {
      const scale = shown.width / shown.pageWidthPt;
      box.scrollTo({ top: Math.max(target.y * scale - 80, 0), behavior: "smooth" });
    }
  }, [source, shown, box]);

  const onPage = source && ready ? source.highlights.filter((h) => h.page === shown.page) : [];
  const otherPages = source ? [...new Set(source.highlights.map((h) => h.page))].filter((p) => p !== page) : [];

  return (
    <section className="pdf-viewer">
      <header>
        {source ? <strong>{source.label}</strong> : <span className="hint">No source selected</span>}
        {source && (
          <span>
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page">◀</button>
            p{page} / {numPages || "…"}
            <button type="button" disabled={numPages > 0 && page >= numPages} onClick={() => setPage(page + 1)} aria-label="Next page">▶</button>
          </span>
        )}
        <span className="zoom">
          <button type="button" disabled={zoom === ZOOMS[0]} aria-label="Zoom out"
                  onClick={() => setZoom(ZOOMS[Math.max(ZOOMS.indexOf(zoom) - 1, 0)])}>−</button>
          {Math.round(zoom * 100)}%
          <button type="button" disabled={zoom === ZOOMS[ZOOMS.length - 1]} aria-label="Zoom in"
                  onClick={() => setZoom(ZOOMS[Math.min(ZOOMS.indexOf(zoom) + 1, ZOOMS.length - 1)])}>+</button>
        </span>
        {needsRender && <span className="hint">Rendering…</span>}
        {otherPages.length > 0 && <span className="hint">also highlighted on p{otherPages.join(", p")}</span>}
      </header>

      {source?.kind === "computed" && <pre className="computed">{source.text}</pre>}

      <div className="pdf-scroll" ref={setBox}>
        {!source ? (
          <p className="hint empty">Click a card, a bar, a sentence or a source to see it highlighted in the report.</p>
        ) : (
          <Document file={pdfUrl(source.doc_id)} onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
                    loading={<p className="hint">Opening {source.label.split(" p")[0]}…</p>}>
            <div className="page-wrap">
              {ready && (
                <>
                  <Page key={`shown-${shown.page}-${shown.width}`} pageNumber={shown.page} width={shown.width}
                        renderAnnotationLayer={false} loading="" />
                  {onPage.map((h, i) => <Box key={i} h={h} scale={shown.width / shown.pageWidthPt} />)}
                </>
              )}
              {needsRender && (
                // 보이지 않게 그린다. 다 그려지면 onRendered가 위의 "보이는 페이지"와 바꿔 끼운다
                <div className={ready ? "page-pending" : undefined}>
                  <Page key={`pending-${docId}-${page}-${width}`} pageNumber={page} width={width}
                        renderAnnotationLayer={false} loading=""
                        onLoadSuccess={(p) => { pendingWidthPt.current = p.originalWidth; }}
                        onRenderSuccess={onRendered} />
                </div>
              )}
            </div>
          </Document>
        )}
      </div>
    </section>
  );
}

function Box({ h, scale }: { h: Highlight; scale: number }) {
  const [x0, y0, x1, y1] = h.bbox;
  return (
    <div className={`hl hl-${h.level}`}
         style={{ left: x0 * scale, top: y0 * scale, width: (x1 - x0) * scale, height: (y1 - y0) * scale }} />
  );
}