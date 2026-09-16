// ELK owns automatic layout. The Atlas hands the Eclipse Layout Kernel
// (elkjs) the same containment hierarchy React Flow renders -- Search
// clusters as compound nodes, composites, compound units, and leaf units
// -- plus the projected direct-upstream edges, and reads back per-execution
// coordinates AND per-edge routes: the layered algorithm routes every
// edge orthogonally around the sibling nodes of its scope, so a route
// never crosses a card the layout knows about. Every projected edge
// joins two siblings, so it lives in the scope that owns both and
// constrains that scope's layers and spacing. ELK returns child
// positions and edge sections RELATIVE to their container, which is
// exactly React Flow's parentId coordinate space, so node positions map
// one to one and edge points become absolute by adding the container's
// absolute origin. Layout runs only when the structure signature changes
// or the user asks for it back (see AtlasCanvas); pure data refreshes
// never re-enter this module.
import type { ElkNode } from "elkjs/lib/elk.bundled.js";
import type { AtlasNode } from "./graph";
import type { RoutePoint, TopologyEdgeData } from "./TopologyEdge";
import type { Edge } from "@xyflow/react";

// The kernel is heavy, so it loads as its own async chunk on the first
// layout instead of riding in the entry bundle.
const elkLoaded = import("elkjs/lib/elk.bundled.js").then(
  (module) => new module.default(),
);

// Room the cluster header (label, status, counters) needs above children,
// room the composite card's content block (uid, metric hero, foot) needs
// above its WorkUnits, and room a compound unit's header needs above the
// children it owns. Each matches the fixed block the node renders.
export const SEARCH_PADDING = { top: 64, left: 28, bottom: 28, right: 28 };
export const COMPOSITE_PADDING = { top: 112, left: 16, bottom: 14, right: 16 };
export const UNIT_PADDING = { top: 58, left: 12, bottom: 12, right: 12 };

function padding(box: {
  top: number;
  left: number;
  bottom: number;
  right: number;
}): string {
  return `[top=${box.top.toFixed(1)},left=${box.left.toFixed(1)},bottom=${box.bottom.toFixed(1)},right=${box.right.toFixed(1)}]`;
}

// Every scope is laid out as its own layered graph (SEPARATE_CHILDREN):
// inside one scope the disconnected pieces -- the setup chain, the
// composite web, child scopes -- are packed toward a wide aspect ratio
// instead of stacking into one tall column. Edges live in the scope that
// owns both endpoints; the projection in graph.ts guarantees every edge
// has one.
const SCOPE_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.spacing.nodeNodeBetweenLayers": "56",
  "elk.spacing.nodeNode": "36",
  "elk.spacing.edgeNode": "16",
  "elk.spacing.edgeEdge": "10",
  "elk.spacing.componentComponent": "44",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.separateConnectedComponents": "true",
  "elk.aspectRatio": "1.8",
};

const LAYOUT_OPTIONS: Record<string, string> = {
  ...SCOPE_OPTIONS,
  "elk.hierarchyHandling": "SEPARATE_CHILDREN",
};

export type PlacedBox = { x: number; y: number; width: number; height: number };

export type LaidGraph = { nodes: AtlasNode[]; edges: Edge[] };

export async function layoutGraph(
  nodes: AtlasNode[],
  edges: Edge[],
): Promise<LaidGraph> {
  const childrenOf = new Map<string | undefined, AtlasNode[]>();
  for (const node of nodes) {
    const siblings = childrenOf.get(node.parentId) ?? [];
    siblings.push(node);
    childrenOf.set(node.parentId, siblings);
  }
  const parentOf = new Map(nodes.map((node) => [node.id, node.parentId]));
  const edgesByContainer = new Map<string | undefined, ElkNode["edges"]>();
  for (const edge of edges) {
    const container = parentOf.get(edge.source);
    if (container !== parentOf.get(edge.target)) {
      throw new Error(`Atlas edge ${edge.id} crosses an ownership scope`);
    }
    const list = edgesByContainer.get(container) ?? [];
    list?.push({ id: edge.id, sources: [edge.source], targets: [edge.target] });
    edgesByContainer.set(container, list);
  }
  const edgesOf = (
    id: string | undefined,
  ): NonNullable<ElkNode["edges"]> => edgesByContainer.get(id) ?? [];
  const toElk = (node: AtlasNode): ElkNode => {
    const children = childrenOf.get(node.id) ?? [];
    if (children.length === 0) {
      return { id: node.id, width: node.width, height: node.height };
    }
    if (node.type === "composite" || node.type === "unit") {
      // Children stack DOWN inside the card so the card stays narrow and
      // the chain reads top to bottom under the fixed header block.
      const minimum = node.type === "composite" ? "(236,118)" : "(176,58)";
      return {
        id: node.id,
        layoutOptions: {
          "elk.direction": "DOWN",
          "elk.edgeRouting": "ORTHOGONAL",
          "elk.padding": padding(
            node.type === "composite" ? COMPOSITE_PADDING : UNIT_PADDING,
          ),
          "elk.spacing.nodeNode": "14",
          "elk.layered.spacing.nodeNodeBetweenLayers": "18",
          "elk.nodeSize.minimum": minimum,
          "elk.nodeSize.constraints": "MINIMUM_SIZE",
        },
        children: children.map(toElk),
        edges: edgesOf(node.id),
      };
    }
    return {
      id: node.id,
      layoutOptions: { ...SCOPE_OPTIONS, "elk.padding": padding(SEARCH_PADDING) },
      children: children.map(toElk),
      edges: edgesOf(node.id),
    };
  };
  const graph: ElkNode = {
    id: "atlas-root",
    layoutOptions: LAYOUT_OPTIONS,
    children: (childrenOf.get(undefined) ?? []).map(toElk),
    edges: edgesOf(undefined),
  };
  const elk = await elkLoaded;
  const laid = await elk.layout(graph);
  const placed = new Map<string, PlacedBox>();
  const absoluteOf = new Map<string, RoutePoint>();
  const routeOf = new Map<string, RoutePoint[]>();
  // Positions and edge sections are relative to their container; walking
  // with the container's absolute origin turns both into one canvas space.
  const collect = (elkNode: ElkNode, origin: RoutePoint): void => {
    for (const edge of elkNode.edges ?? []) {
      const sections = edge.sections ?? [];
      const points = sections.flatMap((section) => [
        section.startPoint,
        ...(section.bendPoints ?? []),
        section.endPoint,
      ]);
      if (points.length < 2) {
        throw new Error(`ELK returned no route for Atlas edge ${edge.id}`);
      }
      routeOf.set(
        edge.id,
        points.map((point) => ({ x: point.x + origin.x, y: point.y + origin.y })),
      );
    }
    for (const child of elkNode.children ?? []) {
      if (
        child.x === undefined ||
        child.y === undefined ||
        child.width === undefined ||
        child.height === undefined
      ) {
        throw new Error(`ELK returned incomplete placement for ${child.id}`);
      }
      placed.set(child.id, {
        x: child.x,
        y: child.y,
        width: child.width,
        height: child.height,
      });
      const absolute = { x: origin.x + child.x, y: origin.y + child.y };
      absoluteOf.set(child.id, absolute);
      collect(child, absolute);
    }
  };
  collect(laid, { x: 0, y: 0 });
  const laidEdges = edges.map((edge) => {
    const points = routeOf.get(edge.id);
    const source = absoluteOf.get(edge.source);
    const target = absoluteOf.get(edge.target);
    if (points === undefined || source === undefined || target === undefined) {
      throw new Error(`ELK did not route Atlas edge ${edge.id}`);
    }
    const data: TopologyEdgeData = { points, laid: { source, target } };
    return { ...edge, data };
  });
  const laidNodes = nodes.map((node) => {
    const box = placed.get(node.id);
    if (box === undefined) {
      throw new Error(`ELK did not place Atlas node ${node.id}`);
    }
    if ((childrenOf.get(node.id) ?? []).length > 0 || node.type === "search") {
      // Compounds take their ELK-computed extent.
      return {
        ...node,
        position: { x: box.x, y: box.y },
        width: box.width,
        height: box.height,
        style: { width: box.width, height: box.height },
      };
    }
    return { ...node, position: { x: box.x, y: box.y } };
  });
  return { nodes: laidNodes, edges: laidEdges };
}
