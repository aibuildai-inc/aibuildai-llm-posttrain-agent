import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { Components, Options } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeShikiFromHighlighter from "@shikijs/rehype/core";
import remarkGemoji from "remark-gemoji";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { HighlighterCore } from "shiki/core";
import { AIBUILDAI_SHIKI_THEME, loadHighlighter } from "../shiki";
import { CodeBlock } from "./CodeBlock";
import "katex/dist/katex.min.css";

const SCHEMA = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "details", "summary"],
};

const COMPONENTS: Components = {
  pre: CodeBlock,
  table: ({ node, ...rest }) => {
    void node;
    return (
      <div className="table-scroll">
        <table {...rest} />
      </div>
    );
  },
};

function useHighlighter(): HighlighterCore | null {
  const [highlighter, setHighlighter] = useState<HighlighterCore | null>(null);
  useEffect(() => {
    let mounted = true;
    void loadHighlighter().then((loaded) => {
      if (mounted) setHighlighter(loaded);
    });
    return () => {
      mounted = false;
    };
  }, []);
  return highlighter;
}

export function MarkdownSurface(props: { markdown: string }): ReactNode {
  const highlighter = useHighlighter();
  const rehypePlugins = useMemo<Options["rehypePlugins"]>(
    () =>
      highlighter === null
        ? [rehypeRaw, [rehypeSanitize, SCHEMA], rehypeKatex]
        : [
            rehypeRaw,
            [rehypeSanitize, SCHEMA],
            [
              rehypeShikiFromHighlighter,
              highlighter,
              {
                theme: AIBUILDAI_SHIKI_THEME,
                addLanguageClass: true,
                defaultLanguage: "text",
                fallbackLanguage: "text",
              },
            ],
            rehypeKatex,
          ],
    [highlighter],
  );
  return (
    <div className="markdown-surface">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkGemoji]}
        rehypePlugins={rehypePlugins}
        components={COMPONENTS}
      >
        {props.markdown}
      </ReactMarkdown>
    </div>
  );
}
