// Read-only source: the page's one Shiki highlighter turns the text into
// a hast tree, and hast-util-to-jsx-runtime renders that tree as React
// elements — the same AST path MarkdownSurface uses, no HTML string ever
// reaches the DOM. A language Shiki does not bundle renders as plain
// text in the same surface.
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { jsx, jsxs } from "react/jsx-runtime";
import { toJsxRuntime } from "hast-util-to-jsx-runtime";
import { AIBUILDAI_SHIKI_THEME, loadHighlighter } from "../shiki";

export function SourceView(props: { text: string; language: string | null }): ReactNode {
  const [rendered, setRendered] = useState<{ key: string; node: ReactNode } | null>(null);
  const { text, language } = props;
  const key = `${language ?? ""}\n${text}`;
  useEffect(() => {
    let live = true;
    if (language === null) return;
    void loadHighlighter().then((highlighter) => {
      if (!live || !highlighter.getLoadedLanguages().includes(language)) return;
      const tree = highlighter.codeToHast(text, { lang: language, theme: AIBUILDAI_SHIKI_THEME });
      setRendered({ key, node: toJsxRuntime(tree, { Fragment, jsx, jsxs }) });
    });
    return () => {
      live = false;
    };
  }, [key, text, language]);
  const lines = text.split("\n");
  return (
    <div className="ws-source markdown-surface">
      <div className="ws-gutter num" aria-hidden>
        {lines.map((_, i) => (
          <span key={i}>{i + 1}</span>
        ))}
      </div>
      {rendered !== null && rendered.key === key ? (
        rendered.node
      ) : (
        <pre>
          <code>{text}</code>
        </pre>
      )}
    </div>
  );
}
