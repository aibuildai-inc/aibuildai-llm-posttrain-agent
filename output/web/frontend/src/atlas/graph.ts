// Build the Atlas graph from the polled RunResponse.
//
// The run arrives as a flat execution list with durable journal paths and,
// on each execution, the recorded direct upstream paths. This module turns
// it into React Flow nodes and edges once per poll: Search executions
// become compound cluster nodes that own visual space, composite Composites
// become prominent cards inside their Search, WorkUnits become compact
// family-shaped nodes (an Agent that owns children becomes a compound
// card with a fixed header). The two recorded relationships stay separate:
// containment is parent placement (never an edge), and topology is the
// direct-upstream relation, drawn as directed edges. Every exact edge is
// projected into the ownership scope both endpoints share, so the layout
// only ever sees edges between siblings: a Reviser in coder_candidate_1 feeding a
// Coder in coder_candidate_2 is drawn as coder_candidate_1 -> coder_candidate_2 at the Search scope, and
// as the exact WorkUnit flow inside each composite. Journal order
// never states that one sibling depends on another. The structure
// signature lets the canvas relayout only when the graph SHAPE changes --
// status, metric, cost, and elapsed updates keep the last layout.
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { ExecutionSummary, RunResponse } from "../api/client";
import { bestMetric } from "../metric";

export type UnitFamily = Exclude<ExecutionSummary["family"], "search" | "composite">;

// The flow direction of the scope a node sits in, which decides where its
// ports are and how ELK lays its siblings out. A Search scope flows RIGHT;
// the inside of a composite card or a compound unit stacks DOWN.
export type FlowDirection = "RIGHT" | "DOWN";

export type PhaseTick = { key: string; token: string };

export type SearchNodeData = {
  summary: ExecutionSummary;
  resultCount: number;
  doneCount: number;
  activeCount: number;
  failedCount: number;
  bestMetric: number | null;
  remainingS: number | null;
  totalCost: number | null;
  depth: number;
  direction: FlowDirection;
  onFocusScope: (path: string) => void;
  isSelectedResult: boolean;
  [key: string]: unknown;
};

export type CompositeNodeData = {
  summary: ExecutionSummary;
  isSelectedResult: boolean;
  metricName: string;
  phases: PhaseTick[];
  direction: FlowDirection;
  [key: string]: unknown;
};

export type UnitNodeData = {
  summary: ExecutionSummary;
  family: UnitFamily;
  compound: boolean;
  modelsRouted: boolean;
  direction: FlowDirection;
  isSelectedResult: boolean;
  [key: string]: unknown;
};

export type AtlasNode =
  | Node<SearchNodeData, "search">
  | Node<CompositeNodeData, "composite">
  | Node<UnitNodeData, "unit">;

export type AtlasGraph = {
  nodes: AtlasNode[];
  edges: Edge[];
  signature: string;
  // The exact recorded relation, by path, for selection context: what
  // each execution consumes and what consumes it.
  upstreamOf: Map<string, string[]>;
  downstreamOf: Map<string, string[]>;
};

// Leaf sizes the layout engine works with. Compounds (Searches, composites
// that own WorkUnits, and units that own children) get no fixed size:
// ELK derives their extent from their children plus padding.
export const COMPOSITE_SIZE = { width: 354, height: 118 };
export const UNIT_SIZE = { width: 264, height: 58 };
export const EMPTY_SEARCH_SIZE = { width: 400, height: 64 };

const ARROW = {
  type: MarkerType.ArrowClosed,
  width: 14,
  height: 14,
  color: "var(--aibuildai-guide)",
};

function descendantStats(
  root: ExecutionSummary,
  childrenOf: Map<string | null, ExecutionSummary[]>,
): { done: number; active: number; failed: number; metrics: number[] } {
  let done = 0;
  let active = 0;
  let failed = 0;
  // A scored execution says so on its own summary, so the scope's metrics are
  // read off the descendants themselves rather than from a list the Search
  // was asked to publish.
  const metrics: number[] = [];
  const queue = [...(childrenOf.get(root.path) ?? [])];
  while (queue.length > 0) {
    const summary = queue.pop();
    if (summary === undefined) {
      throw new Error("Atlas descendant queue lost an execution");
    }
    if (summary.status === "done") done += 1;
    if (summary.status === "running") active += 1;
    if (summary.status === "failed") failed += 1;
    if (summary.score !== null) metrics.push(summary.score.score);
    queue.push(...(childrenOf.get(summary.path) ?? []));
  }
  return { done, active, failed, metrics };
}

function ownershipChain(
  path: string,
  byPath: Map<string, ExecutionSummary>,
): string[] {
  const summary = byPath.get(path);
  if (summary === undefined) throw new Error(`unknown occurrence ${path}`);
  return summary.parent_path === null
    ? [path]
    : [...ownershipChain(summary.parent_path, byPath), path];
}

export type ProjectedEdge = {
  source: string;
  target: string;
  // The scope both visible endpoints sit in: undefined at the root.
  scope: string | undefined;
};

// Project one exact edge to siblings; containment represents owner-to-child.
export function projectEdge(
  source: string,
  target: string,
  byPath: Map<string, ExecutionSummary>,
): ProjectedEdge | null {
  const sourceChain = ownershipChain(source, byPath);
  const targetChain = ownershipChain(target, byPath);
  let shared = 0;
  while (
    shared < sourceChain.length &&
    shared < targetChain.length &&
    sourceChain[shared] === targetChain[shared]
  ) {
    shared += 1;
  }
  const visibleSource = sourceChain[shared];
  const visibleTarget = targetChain[shared];
  if (visibleSource === undefined || visibleTarget === undefined) {
    return null;
  }
  return {
    source: visibleSource,
    target: visibleTarget,
    scope: shared === 0 ? undefined : sourceChain[shared - 1],
  };
}

export function buildGraph(
  run: RunResponse,
  onFocusScope: (path: string) => void,
): AtlasGraph {
  const childrenOf = new Map<string | null, ExecutionSummary[]>();
  const byPath = new Map<string, ExecutionSummary>();
  for (const summary of run.executions) {
    const siblings = childrenOf.get(summary.parent_path) ?? [];
    siblings.push(summary);
    childrenOf.set(summary.parent_path, siblings);
    byPath.set(summary.path, summary);
  }
  const nodes: AtlasNode[] = [];
  const upstreamOf = new Map<string, string[]>();
  const downstreamOf = new Map<string, string[]>();

  // Parents must precede children in the React Flow node array. Siblings
  // keep their recorded order so changing a metric never changes position.
  const walk = (
    summary: ExecutionSummary,
    depth: number,
    direction: FlowDirection,
  ): void => {
    const children = childrenOf.get(summary.path) ?? [];
    const parentFields =
      summary.parent_path === null
        ? {}
        : { parentId: summary.parent_path, extent: "parent" as const };
    let inner: FlowDirection = "RIGHT";
    if (summary.family === "search") {
      const stats = descendantStats(summary, childrenOf);
      nodes.push({
        id: summary.path,
        type: "search",
        position: { x: 0, y: 0 },
        ...(children.length === 0 ? EMPTY_SEARCH_SIZE : {}),
        ...parentFields,
        data: {
          summary,
          resultCount: stats.metrics.length,
          doneCount: stats.done,
          activeCount: stats.active,
          failedCount: stats.failed,
          bestMetric: bestMetric(stats.metrics, run.metric_direction),
          remainingS: depth === 0 ? run.remaining_s : null,
          totalCost: depth === 0 ? run.total_cost : null,
          depth,
          direction,
          onFocusScope,
          isSelectedResult: run.selected_result_path === summary.path,
        },
      });
    } else if (summary.family === "composite") {
      inner = "DOWN";
      const phases: PhaseTick[] = children
        .filter((c) => c.family !== "search" && c.family !== "composite")
        .map((c) => ({
          key: c.path,
          token: c.status_style_token,
        }));
      nodes.push({
        id: summary.path,
        type: "composite",
        position: { x: 0, y: 0 },
        // A composite that owns children (WorkUnits, or a nested Search)
        // is a compound: they lay out inside the card, below the fixed
        // content block.
        ...(children.length === 0 ? COMPOSITE_SIZE : {}),
        ...parentFields,
        data: {
          summary,
          isSelectedResult: run.selected_result_path === summary.path,
          metricName: run.metric_name,
          phases,
          direction,
        },
      });
    } else {
      inner = "DOWN";
      const compound = children.length > 0;
      nodes.push({
        id: summary.path,
        type: "unit",
        position: { x: 0, y: 0 },
        // A unit that owns children is a
        // compound: a fixed header, and the children below it.
        ...(compound ? {} : UNIT_SIZE),
        ...parentFields,
        data: {
          summary,
          family: summary.family,
          compound,
          modelsRouted: run.model === "routed",
          direction,
          isSelectedResult: run.selected_result_path === summary.path,
        },
      });
    }
    for (const child of children) walk(child, depth + 1, inner);
  };
  for (const root of childrenOf.get(null) ?? []) walk(root, 0, "RIGHT");
  if (nodes.length !== run.executions.length) {
    throw new Error("Atlas graph has an execution without its parent scope");
  }

  // Project exact Action edges to visible siblings. A pair is lineage only
  // when one exact Composite-to-Composite edge contributes to it.
  const projected = new Map<
    string,
    { source: string; target: string; scope: string | undefined; lineage: boolean }
  >();
  for (const summary of run.executions) {
    upstreamOf.set(summary.path, [...summary.upstream_actions]);
    for (const source of summary.upstream_actions) {
      if (!byPath.has(source)) {
        throw new Error(`${summary.path} has missing upstream ${source}`);
      }
      const consumers = downstreamOf.get(source) ?? [];
      consumers.push(summary.path);
      downstreamOf.set(source, consumers);
      const edge = projectEdge(source, summary.path, byPath);
      if (edge === null) continue;
      const id = `flow:${edge.source}->${edge.target}`;
      const exactLineage =
        byPath.get(source)?.family === "composite" && summary.family === "composite";
      const entry = projected.get(id);
      if (entry === undefined) {
        projected.set(id, { ...edge, lineage: exactLineage });
      } else {
        entry.lineage = entry.lineage || exactLineage;
      }
    }
  }
  const edges = [...projected.entries()].map(([id, entry]) => {
    const scopeFamily =
      entry.scope === undefined ? "search" : byPath.get(entry.scope)?.family;
    return {
      id,
      source: entry.source,
      target: entry.target,
      type: "topology",
      markerEnd: ARROW,
      className: [
        entry.lineage ? "edge-lineage" : "edge-flow",
        scopeFamily === "search" ? "edge-scope" : "edge-inner",
        // A living indicator: the edge into a running consumer marches.
        ...(byPath.get(entry.target)?.status === "running" ? ["edge-live"] : []),
      ].join(" "),
    };
  });

  const signature =
    nodes
      .map((n) => `${n.id}<${n.parentId ?? ""}`)
      .sort()
      .join("|") +
    "#" +
    edges
      .map((e) => e.id)
      .sort()
      .join("|");
  return { nodes, edges, signature, upstreamOf, downstreamOf };
}

// Every path reachable from ``start`` through one adjacency, itself excluded.
export function closure(
  adjacency: Map<string, string[]>,
  start: string,
): Set<string> {
  const seen = new Set<string>();
  const queue = [...(adjacency.get(start) ?? [])];
  while (queue.length > 0) {
    const next = queue.pop();
    if (next === undefined) throw new Error("Atlas closure lost a path");
    if (seen.has(next)) continue;
    seen.add(next);
    queue.push(...(adjacency.get(next) ?? []));
  }
  return seen;
}
