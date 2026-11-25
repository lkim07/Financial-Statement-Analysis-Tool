// 오른쪽: PDF 한 페이지 + 하이라이트 사각형.
//
// 좌표 변환: 화면 px = PDF 포인트 × 배율, 배율 = 화면에 그린 폭(px) / 페이지 원래 폭(pt).
// - 화면 폭: 이 칸의 실제 폭을 ResizeObserver로 잰다 (창 크기가 바뀌면 다시 잰다)
// - 페이지 원래 폭: API에는 없다. react-pdf가 페이지를 읽을 때 알려 준다 (onLoadSuccess의 originalWidth)
// 좌표는 PyMuPDF 기준(왼쪽 위 원점)이라 y를 뒤집지 않는다.
import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import type { Highlight, Source } from "./api";
import { firstTarget, pdfUrl } from "./api";

// worker 설정은 react-pdf를 쓰는 바로 이 파일에 둬야 한다 (react-pdf README)
pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

type Props = { source: Source | null };

export function PdfViewer({ source }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [pageWidthPt, setPageWidthPt] = useState<number | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);

  // 칸의 폭을 잰다
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.min(entry.contentRect.width - 24, 1000)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // 출처가 바뀌면 그 출처의 첫 하이라이트 페이지로.
  // effect 안에서 setState를 하면 한 번 더 그려진다: 렌더링 중에 "이전 출처"와 비교해서 바로 바꾼다
  // (React 문서의 "Adjusting some state when a prop changes" 패턴)
  const [shownSource, setShownSource] = useState<Source | null>(null);
  if (source !== shownSource) {
    setShownSource(source);
    if (source) setPage(firstTarget(source).page);
  }

  const scale = pageWidthPt ? width / pageWidthPt : null;

  // 페이지를 그린 뒤 첫 하이라이트 높이로 스크롤
  useEffect(() => {
    if (!source || !scale || !scrollRef.current) return;
    const target = firstTarget(source);
    if (target.page === page) {
      scrollRef.current.scrollTo({ top: Math.max(target.y * scale - 80, 0), behavior: "smooth" });
    }
  }, [source, scale, page]);

  if (!source) {
    return (
      <section className="pdf-viewer empty" ref={scrollRef}>
        <p>Click a sentence or a source to see it highlighted in the report.</p>
      </section>
    );
  }

  const onPage = source.highlights.filter((h) => h.page === page);
  const otherPages = [...new Set(source.highlights.map((h) => h.page))].filter((p) => p !== page);

  return (
    <section className="pdf-viewer">
      <header>
        <strong>{source.label}</strong>
        <span>
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            ◀
          </button>
          p{page} / {numPages || "…"}
          <button type="button" disabled={numPages > 0 && page >= numPages} onClick={() => setPage(page + 1)}>
            ▶
          </button>
        </span>
        {otherPages.length > 0 && <span className="hint">also highlighted on p{otherPages.join(", p")}</span>}
      </header>

      {source.kind === "computed" && <pre className="computed">{source.text}</pre>}

      <div className="pdf-scroll" ref={scrollRef}>
        <Document
          file={pdfUrl(source.doc_id)}
          onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
          loading={<p className="hint">Loading PDF…</p>}
        >
          <div className="page-wrap">
            <Page
              pageNumber={page}
              width={width}
              renderAnnotationLayer={false}
              onLoadSuccess={(p) => setPageWidthPt(p.originalWidth)}
            />
            {scale && onPage.map((h, i) => <Box key={i} h={h} scale={scale} />)}
          </div>
        </Document>
      </div>
    </section>
  );
}

function Box({ h, scale }: { h: Highlight; scale: number }) {
  const [x0, y0, x1, y1] = h.bbox;
  return (
    <div
      className={`hl hl-${h.level}`}
      style={{ left: x0 * scale, top: y0 * scale, width: (x1 - x0) * scale, height: (y1 - y0) * scale }}
    />
  );
}