import { Children, isValidElement, type ComponentProps, type ReactNode } from "react";
import type { ExtraProps } from "react-markdown";
import { MermaidBlock } from "./MermaidBlock";

function languageOf(node: ExtraProps["node"]): string | null {
  for (const child of node?.children ?? []) {
    if (child.type !== "element") continue;
    const raw: unknown = child.properties?.className ?? child.properties?.class;
    const classes = Array.isArray(raw)
      ? raw
      : typeof raw === "string"
        ? raw.split(" ")
        : [];
    for (const value of classes) {
      if (typeof value === "string" && value.startsWith("language-")) {
        const language = value.slice("language-".length);
        return language === "text" || language === "plaintext" ? null : language;
      }
    }
  }
  return null;
}

function textOf(children: ReactNode): string {
  const parts: string[] = [];
  Children.forEach(children, (child) => {
    if (typeof child === "string") parts.push(child);
    else if (typeof child === "number") parts.push(String(child));
    else if (isValidElement(child)) {
      const p = child.props as Record<string, unknown>;
      if (p.children != null) parts.push(textOf(p.children as ReactNode));
    }
  });
  return parts.join("");
}

const MERMAID_STARTS = /^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|gitGraph|gitgraph|mindmap|timeline|journey|quadrantChart|xychart|sankey|block-beta|packet-beta|architecture-beta|kanban)\b/i;

function isMermaidContent(code: string): boolean {
  const trimmed = code.trim();
  return MERMAID_STARTS.test(trimmed);
}

export function CodeBlock(props: ComponentProps<"pre"> & ExtraProps): ReactNode {
  const { node, children, className, ...rest } = props;
  const language = languageOf(node);
  const code = textOf(children).trim();

  if (language === "mermaid" || (language === null && isMermaidContent(code))) {
    return <MermaidBlock code={code} />;
  }

  return (
    <figure className="code-surface">
      {language !== null && (
        <figcaption className="code-language">{language}</figcaption>
      )}
      <pre {...rest} className={className}>
        {children}
      </pre>
    </figure>
  );
}
