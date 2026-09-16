// The Execution Atlas canvas. React Flow owns every viewport mechanism —
// pan, zoom, MiniMap, fit-view controls, selection — and the graph never
// changes through it: no connecting, no deleting. Positions have one
// authority with one user override: ELK computes the layout, dragging a
// node moves it (and what it owns) inside its owner, and the override
// lives until the next structural ELK pass or the layout control button
// puts the computed layout back. Layout is gated on the structure
// signature: a poll that only moves status, cost, metric, or elapsed
// merges new data into the laid-out nodes in place — dragged positions
// included — while a poll that adds an execution (or an upstream edge)
// runs ELK once and lets CSS glide existing nodes to their new
// positions. The viewport is fitted exactly once, on the first laid-out
// graph — later polls never move the camera or steal the selection.
// Selecting an object dims what is unrelated (its owners, its owned
// subtree, everything upstream of it, and everything downstream of it
// stay lit); hovering an object lights the edges it takes part in; the
// camera nudges ONLY when a selected object sits outside the viewport;
// double-clicking a Search focuses its scope and feeds the breadcrumb.
// The top-left context strip holds three separate controls:
// the root-scope button (disabled at the root, so it is never a clickable
// no-op), the run selector (which run the page shows), and the Search
// breadcrumb (where inside that run).
import {
  applyNodeChanges,
  Background,
  BackgroundVariant,
  ControlButton,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ChevronRight, LayoutDashboard, Map as MapIcon } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "react-aria-components";
import type { RunResponse } from "../api/client";
import { statusColor } from "../components/StatusBadge";
import { buildGraph, closure, type AtlasGraph, type AtlasNode } from "./graph";
import { layoutGraph } from "./layout";
import { CompositeCardNode } from "./nodes/CompositeCardNode";
import { SearchClusterNode } from "./nodes/SearchClusterNode";
import { TopologyEdge } from "./TopologyEdge";
import { UnitNode } from "./nodes/UnitNode";

const NODE_TYPES = {
  search: SearchClusterNode,
  composite: CompositeCardNode,
  unit: UnitNode,
};

const EDGE_TYPES = { topology: TopologyEdge };

// Semantic zoom: the viewport scale selects an information density tier.
// The tier only switches a data attribute — CSS reveals or hides detail,
// the domain graph and the layout never change with zoom.
function tierOf(zoom: number): "overview" | "search" | "execution" {
  if (zoom < 0.35) return "overview";
  if (zoom < 0.95) return "search";
  return "execution";
}

function descendsFrom(
  parentOf: Map<string, string | undefined>,
  id: string,
  ancestor: string,
): boolean {
  let cursor = parentOf.get(id);
  while (cursor !== undefined) {
    if (cursor === ancestor) return true;
    cursor = parentOf.get(cursor);
  }
  return false;
}

// The selection's lit context: itself, the scopes that own it, its whole
// owned subtree, its complete upstream ancestry, and its complete
// downstream descendants, both read off the exact recorded relation.
// Every execution in that topology closure is lifted to its full
// ownership chain: an inner upstream unit keeps the Composite and Search
// cards that own it lit, so the projected edges that stand for it stay
// lit too. Everything else dims without disappearing.
export function contextOf(graph: AtlasGraph, selected: string): Set<string> {
  const parentOf = new Map(graph.nodes.map((n) => [n.id, n.parentId]));
  const lit = new Set<string>();
  const litWithOwners = (path: string): void => {
    lit.add(path);
    let cursor = parentOf.get(path);
    while (cursor !== undefined) {
      lit.add(cursor);
      cursor = parentOf.get(cursor);
    }
  };
  litWithOwners(selected);
  for (const node of graph.nodes) {
    if (descendsFrom(parentOf, node.id, selected)) lit.add(node.id);
  }
  for (const path of closure(graph.upstreamOf, selected)) litWithOwners(path);
  for (const path of closure(graph.downstreamOf, selected)) litWithOwners(path);
  return lit;
}

// The run's scope crumbs, placed after the Workspace navigation: the root
// control returns to this run's Search root, never to the all-run home.
function Breadcrumb(props: {
  scope: string | null;
  onFocus: (path: string | null) => void;
  navigation: ReactNode;
}): ReactNode {
  const segments =
    props.scope === null
      ? []
      : props.scope
          .split("/")
          .map((_, index, parts) => parts.slice(0, index + 1).join("/"));
  return (
    <nav className="scope-breadcrumb" aria-label="Run and Search scope">
      {props.navigation}
      <Button
        className="scope-crumb scope-root"
        isDisabled={props.scope === null}
        onPress={() => props.onFocus(null)}
      >
        <MapIcon size={12} strokeWidth={1.75} aria-hidden />
        root
      </Button>
      {segments.map((path) => (
        <span key={path} className="scope-crumb-pair">
          <ChevronRight size={11} strokeWidth={1.75} aria-hidden />
          <Button className="scope-crumb" onPress={() => props.onFocus(path)}>
            {path.split("/").at(-1)}
          </Button>
        </span>
      ))}
    </nav>
  );
}

function Canvas(props: {
  run: RunResponse;
  selectedPath: string | null;
  scopePath: string | null;
  onSelect: (path: string | null) => void;
  onFocusScope: (path: string | null) => void;
  navigation: ReactNode;
}): ReactNode {
  const [nodes, setNodes] = useState<AtlasNode[]>([]);
  const [edges, setEdges] = useState<AtlasGraph["edges"]>([]);
  const [hoveredPath, setHoveredPath] = useState<string | null>(null);
  const [layoutRevision, setLayoutRevision] = useState(0);
  const relayoutRef = useRef<() => void>(() => {
    throw new Error("Atlas layout has not run yet; nothing to restore");
  });
  const signatureRef = useRef("");
  const laidSignatureRef = useRef("");
  const graphRef = useRef<AtlasGraph | null>(null);
  const selectedPathRef = useRef(props.selectedPath);
  const layoutSeq = useRef(0);
  const cameraScopeRef = useRef<string | null | undefined>(undefined);
  const cameraSelectionRef = useRef<string | null | undefined>(undefined);
  const wrapper = useRef<HTMLDivElement | null>(null);
  const { fitView, getInternalNode, getViewport, setCenter } = useReactFlow();
  const tier = useStore((state) => tierOf(state.transform[2]));

  const { run, selectedPath, scopePath } = props;
  useEffect(() => {
    const graph = buildGraph(run, props.onFocusScope);
    graphRef.current = graph;
    selectedPathRef.current = selectedPath;
    const currentContext = (): Set<string> | null => {
      const currentGraph = graphRef.current;
      if (currentGraph === null) {
        throw new Error("Atlas refresh lost its graph facts");
      }
      return selectedPathRef.current === null
        ? null
        : contextOf(currentGraph, selectedPathRef.current);
    };
    const decorate = (laid: AtlasNode[]): AtlasNode[] => {
      const lit = currentContext();
      return laid.map((node) => ({
        ...node,
        selected: node.id === selectedPathRef.current,
        className:
          lit === null || lit.has(node.id) ? undefined : "atlas-dim",
      }));
    };
    const decorateEdges = (
      list: AtlasGraph["edges"],
    ): AtlasGraph["edges"] => {
      const lit = currentContext();
      return list.map((edge) =>
        lit === null || lit.has(edge.source) || lit.has(edge.target)
          ? edge
          : { ...edge, className: `${edge.className ?? ""} atlas-dim`.trim() },
      );
    };
    // One ELK pass, then swap the whole graph in. The sequence guard
    // drops a stale layout that resolves after a newer one. Also the
    // layout control button's action: it recomputes the current
    // structure's layout, which is exactly "put the dragged nodes back".
    const runElk = (): void => {
      const seq = ++layoutSeq.current;
      void layoutGraph(graph.nodes, graph.edges).then((laid) => {
        if (seq !== layoutSeq.current) return;
        const freshGraph = graphRef.current;
        if (freshGraph === null || freshGraph.signature !== graph.signature) {
          throw new Error("Atlas layout lost its graph facts");
        }
        const byId = new Map(freshGraph.nodes.map((node) => [node.id, node]));
        setNodes(
          decorate(
            laid.nodes.map((node) => {
              const fresh = byId.get(node.id);
              if (fresh === undefined) {
                throw new Error(`Atlas layout lost node ${node.id}`);
              }
              return { ...node, data: fresh.data } as AtlasNode;
            }),
          ),
        );
        // Routed geometry from this layout, fresh facts (edge classes)
        // from the newest poll of the same structure.
        const routed = new Map(laid.edges.map((edge) => [edge.id, edge.data]));
        setEdges(
          decorateEdges(
            freshGraph.edges.map((edge) => {
              const data = routed.get(edge.id);
              if (data === undefined) {
                throw new Error(`Atlas layout lost edge ${edge.id}`);
              }
              return { ...edge, data };
            }),
          ),
        );
        laidSignatureRef.current = freshGraph.signature;
        setLayoutRevision((revision) => revision + 1);
      });
    };
    relayoutRef.current = runElk;
    if (graph.signature === signatureRef.current) {
      // Same structure: keep every position — dragged ones included —
      // adopt the fresh facts, and carry each edge's routed geometry.
      const byId = new Map(graph.nodes.map((node) => [node.id, node]));
      setNodes((previous) =>
        decorate(
          previous.map((node) => {
            const fresh = byId.get(node.id);
            if (fresh === undefined) {
              throw new Error(`Atlas refresh lost node ${node.id}`);
            }
            return { ...node, data: fresh.data } as AtlasNode;
          }),
        ),
      );
      setEdges((previous) => {
        const routed = new Map(previous.map((edge) => [edge.id, edge.data]));
        return decorateEdges(
          graph.edges.map((edge) => ({ ...edge, data: routed.get(edge.id) })),
        );
      });
      return;
    }
    signatureRef.current = graph.signature;
    runElk();
  }, [run, selectedPath, props.onFocusScope]);

  // One camera decision runs after each ELK layout has landed in React Flow.
  // A pasted scope link frames that Search, a pasted selection centers that
  // execution, and a URL with neither gets the full-run fit. Later selections
  // move the camera only when they are outside the viewport.
  useEffect(() => {
    if (layoutRevision === 0 || wrapper.current === null) return;
    if (graphRef.current?.signature !== laidSignatureRef.current) return;
    const frame = requestAnimationFrame(() => {
      if (wrapper.current === null) return;
      const previousScope = cameraScopeRef.current;
      const initial = previousScope === undefined;
      const scopeChanged = !initial && previousScope !== scopePath;
      const selectionChanged =
        !initial && cameraSelectionRef.current !== selectedPath;
      cameraScopeRef.current = scopePath;
      cameraSelectionRef.current = selectedPath;
      if (scopePath !== null && (initial || scopeChanged)) {
        void fitView({
          nodes: [{ id: scopePath }],
          padding: 0.15,
          duration: initial ? 0 : 450,
        });
        return;
      }
      if (scopePath !== null) return;
      if ((initial || scopeChanged) && selectedPath === null) {
        void fitView({ padding: 0.12, duration: initial ? 0 : 450 });
        return;
      }
      if (
        selectedPath === null ||
        (!initial && !scopeChanged && !selectionChanged)
      ) {
        return;
      }
      const internal = getInternalNode(selectedPath);
      if (internal === undefined) return;
      const { x, y, zoom } = getViewport();
      const bounds = wrapper.current.getBoundingClientRect();
      const abs = internal.internals.positionAbsolute;
      const width = internal.measured.width ?? internal.width ?? 0;
      const height = internal.measured.height ?? internal.height ?? 0;
      const screenX = abs.x * zoom + x;
      const screenY = abs.y * zoom + y;
      const visible =
        screenX + width * zoom > 0 &&
        screenY + height * zoom > 0 &&
        screenX < bounds.width &&
        screenY < bounds.height;
      if (initial || !visible) {
        void setCenter(abs.x + width / 2, abs.y + height / 2, {
          zoom,
          duration: initial ? 0 : 380,
        });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [
    layoutRevision,
    scopePath,
    selectedPath,
    fitView,
    getInternalNode,
    getViewport,
    setCenter,
  ]);

  // Hovering an object lights every edge it takes part in, on top of the
  // selection dimming already written into the stored edges.
  const shownEdges =
    hoveredPath === null
      ? edges
      : edges.map((edge) =>
          edge.source === hoveredPath || edge.target === hoveredPath
            ? { ...edge, className: `${edge.className ?? ""} edge-hot`.trim() }
            : edge,
        );

  return (
    <div className="atlas" data-tier={tier} ref={wrapper}>
      <ReactFlow
        proOptions={{ hideAttribution: true }}
        nodes={nodes}
        edges={shownEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodeClick={(_, node) => props.onSelect(node.id)}
        onNodeDoubleClick={(_, node) => {
          if (node.type === "search") props.onFocusScope(node.id);
        }}
        onNodeMouseEnter={(_, node) => setHoveredPath(node.id)}
        onNodeMouseLeave={() => setHoveredPath(null)}
        onPaneClick={() => props.onSelect(null)}
        // Dragging and resizing are the user's two overrides; selection
        // stays owned by this component, so only those changes are applied.
        onNodesChange={(changes) => {
          const moves = changes.filter(
            (change) => change.type === "position" || change.type === "dimensions",
          );
          if (moves.length === 0) return;
          setNodes(
            (previous) => applyNodeChanges(moves, previous) as AtlasNode[],
          );
        }}
        onNodeDragStop={() => {}}
        nodesConnectable={false}
        edgesReconnectable={false}
        edgesFocusable={false}
        deleteKeyCode={null}
        selectionOnDrag={false}
        zoomOnDoubleClick={false}
        minZoom={0.15}
        maxZoom={1.75}
        onlyRenderVisibleElements
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={26}
          size={1.4}
          color="var(--aibuildai-atlas-grid)"
        />
        {props.scopePath !== null && (
          <Panel position="top-left">
            <Breadcrumb
              scope={props.scopePath}
              onFocus={props.onFocusScope}
              navigation={props.navigation}
            />
          </Panel>
        )}
        <MiniMap
          pannable
          zoomable
          nodeColor={(node) =>
            node.type === "search"
              ? "var(--aibuildai-atlas-border)"
              : statusColor((node as AtlasNode).data.summary.status_style_token)
          }
          nodeStrokeColor="var(--aibuildai-atlas-border)"
          bgColor="var(--aibuildai-atlas-canvas)"
          maskColor="color-mix(in srgb, var(--aibuildai-atlas-canvas) 72%, transparent)"
        />
        <Controls showInteractive={false}>
          {props.scopePath !== null && (
            <ControlButton
              onClick={() => props.onFocusScope(null)}
              title="Back to root scope"
              aria-label="Back to root scope"
            >
              <MapIcon strokeWidth={1.75} aria-hidden />
            </ControlButton>
          )}
          <ControlButton
            onClick={() => relayoutRef.current()}
            title="Restore the computed layout"
            aria-label="Restore the computed layout"
          >
            <LayoutDashboard strokeWidth={1.75} aria-hidden />
          </ControlButton>
        </Controls>
      </ReactFlow>
    </div>
  );
}

export function AtlasCanvas(props: {
  run: RunResponse;
  selectedPath: string | null;
  scopePath: string | null;
  onSelect: (path: string | null) => void;
  onFocusScope: (path: string | null) => void;
  navigation: ReactNode;
}): ReactNode {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
