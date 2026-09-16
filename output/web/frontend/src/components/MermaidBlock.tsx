import { useEffect, useId, useRef, useState, type ReactNode } from "react";

let mermaidReady: Promise<typeof import("mermaid")> | null = null;

function loadMermaid(): Promise<typeof import("mermaid")> {
  mermaidReady ??= import("mermaid").then((m) => {
    m.default.initialize({
      startOnLoad: false,
      theme: "neutral",
      fontFamily: "Inter Variable, system-ui, sans-serif",
      suppressErrorRendering: true,
    });
    return m;
  });
  return mermaidReady;
}

export function MermaidBlock(props: { code: string }): ReactNode {
  const id = useId().replace(/:/g, "_");
  const container = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void loadMermaid().then(async (m) => {
      if (cancelled) return;
      try {
        const { svg } = await m.default.render(`mermaid-${id}`, props.code);
        if (!cancelled && container.current) {
          container.current.innerHTML = svg;
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    });
    return () => { cancelled = true; };
  }, [props.code, id]);

  if (error !== null) {
    return (
      <figure className="code-surface">
        <figcaption className="code-language">mermaid</figcaption>
        <pre className="mermaid-error">{props.code}</pre>
      </figure>
    );
  }

  return (
    <figure className="mermaid-surface">
      <div ref={container} className="mermaid-diagram" />
    </figure>
  );
}
