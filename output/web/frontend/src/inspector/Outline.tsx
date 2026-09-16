// The Search inspector's outline: the scope's subtree as a tree. A child
// Search is a collapsible group whose row is the group header and whose
// children hang under a guide line, at any depth; a WorkUnit or a
// composite is a leaf row. A click selects the execution on the
// canvas. The tree is the one place the scope's structure is shown, so
// there is no separate child-search or scope-execution list.
import type { ReactNode } from "react";
import { Button } from "react-aria-components";
import type { ExecutionSummary } from "../api/client";
import { ExecutionIcon, executionAnnotation } from "../components/ExecutionIcon";
import { StatusDot } from "../components/StatusBadge";
import { fmtCost, fmtMetric } from "../format";

type OutlineNode = { summary: ExecutionSummary; children: OutlineNode[] };

function buildTree(
  summaries: Map<string, ExecutionSummary>,
  rootPath: string,
): OutlineNode[] {
  const byParent = new Map<string | null, ExecutionSummary[]>();
  for (const summary of summaries.values()) {
    const list = byParent.get(summary.parent_path) ?? [];
    list.push(summary);
    byParent.set(summary.parent_path, list);
  }
  const grow = (path: string): OutlineNode[] =>
    (byParent.get(path) ?? [])
      .sort((a, b) => a.path.localeCompare(b.path))
      .map((summary) => ({ summary, children: grow(summary.path) }));
  return grow(rootPath);
}

function Row(props: {
  summary: ExecutionSummary;
  count?: number;
  onSelect: (path: string) => void;
}): ReactNode {
  const { summary } = props;
  return (
    <Button className="outline-row" onPress={() => props.onSelect(summary.path)}>
      <ExecutionIcon summary={summary} size={12} />
      <span className="outline-label num">{summary.label}</span>
      <span className="outline-kind">{executionAnnotation(summary)}</span>
      {props.count !== undefined && (
        <span className="outline-count num">{props.count}</span>
      )}
      <span className="outline-spacer" />
      {summary.metric !== null && (
        <span className="outline-num num">{fmtMetric(summary.metric)}</span>
      )}
      {summary.cost !== null && summary.cost > 0 && (
        <span className="outline-num num">{fmtCost(summary.cost)}</span>
      )}
      <StatusDot token={summary.status_style_token} />
    </Button>
  );
}

function Branch(props: {
  nodes: OutlineNode[];
  onSelect: (path: string) => void;
}): ReactNode {
  return (
    <ul className="outline-list">
      {props.nodes.map((node) => (
        <li key={node.summary.path}>
          {node.summary.family === "search" ? (
            <details className="outline-group" open>
              <summary>
                <Row
                  summary={node.summary}
                  count={node.children.length}
                  onSelect={props.onSelect}
                />
              </summary>
              <div className="outline-children">
                <Branch nodes={node.children} onSelect={props.onSelect} />
              </div>
            </details>
          ) : (
            <Row summary={node.summary} onSelect={props.onSelect} />
          )}
        </li>
      ))}
    </ul>
  );
}

export function Outline(props: {
  rootPath: string;
  summaries: Map<string, ExecutionSummary>;
  onSelect: (path: string) => void;
}): ReactNode {
  const tree = buildTree(props.summaries, props.rootPath);
  if (tree.length === 0) return null;
  return (
    <nav className="outline" aria-label="scope outline">
      <Branch nodes={tree} onSelect={props.onSelect} />
    </nav>
  );
}
