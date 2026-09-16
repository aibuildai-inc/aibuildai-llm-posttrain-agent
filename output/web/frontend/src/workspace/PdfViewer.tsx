import { useCallback, useState, type ReactNode } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export function PdfViewer(props: { url: string }): ReactNode {
  const [numPages, setNumPages] = useState<number>(0);
  const [error, setError] = useState(false);

  const onLoadSuccess = useCallback(({ numPages: n }: { numPages: number }) => {
    setNumPages(n);
    setError(false);
  }, []);

  if (error) {
    return <p className="quiet ws-empty">PDF could not be loaded — the file may still be written</p>;
  }

  return (
    <div className="pdf-viewer">
      <Document
        file={props.url}
        onLoadSuccess={onLoadSuccess}
        onLoadError={() => setError(true)}
        loading={<p className="quiet ws-empty">loading PDF…</p>}
      >
        {Array.from({ length: numPages }, (_, i) => (
          <Page
            key={i + 1}
            pageNumber={i + 1}
            width={720}
            className="pdf-page"
          />
        ))}
      </Document>
    </div>
  );
}
