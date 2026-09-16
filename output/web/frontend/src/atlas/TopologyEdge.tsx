// The one Atlas edge. When both endpoints sit where ELK placed them
// (or shifted equally as a group), the edge follows the ELK-routed
// polyline with rounded quadratic-arc corners. When a node is dragged
// so the endpoints shift independently, the edge draws a smooth curve
// between the two cards' border points: a horizontal cubic bezier for
// primarily horizontal spans, a two-segment quadratic for vertical.
import {
  BaseEdge,
  useInternalNode,
  type Edge,
  type EdgeProps,
  type InternalNode,
} from "@xyflow/react";
import type { ReactNode } from "react";

export type RoutePoint = { x: number; y: number };

export type TopologyEdgeData = {
  // The routed polyline, absolute canvas coordinates: start, bends, end.
  points: RoutePoint[];
  // Where each endpoint node sat, absolutely, when the route was made.
  laid: { source: RoutePoint; target: RoutePoint };
  [key: string]: unknown;
};

export type TopologyEdgeType = Edge<TopologyEdgeData, "topology">;

const CORNER_RADIUS = 9;

function roundedPath(points: RoutePoint[], shift: RoutePoint): string {
  const at = (point: RoutePoint): RoutePoint => ({
    x: point.x + shift.x,
    y: point.y + shift.y,
  });
  const first = points[0];
  const last = points[points.length - 1];
  if (first === undefined || last === undefined) {
    throw new Error("Atlas edge route has no points");
  }
  let path = `M ${at(first).x} ${at(first).y}`;
  for (let index = 1; index < points.length - 1; index += 1) {
    const before = at(points[index - 1] as RoutePoint);
    const corner = at(points[index] as RoutePoint);
    const after = at(points[index + 1] as RoutePoint);
    const inLength = Math.hypot(corner.x - before.x, corner.y - before.y);
    const outLength = Math.hypot(after.x - corner.x, after.y - corner.y);
    const radius = Math.min(CORNER_RADIUS, inLength / 2, outLength / 2);
    if (radius < 0.5) {
      path += ` L ${corner.x} ${corner.y}`;
      continue;
    }
    const inX = corner.x - ((corner.x - before.x) / inLength) * radius;
    const inY = corner.y - ((corner.y - before.y) / inLength) * radius;
    const outX = corner.x + ((after.x - corner.x) / outLength) * radius;
    const outY = corner.y + ((after.y - corner.y) / outLength) * radius;
    path += ` L ${inX} ${inY} Q ${corner.x} ${corner.y} ${outX} ${outY}`;
  }
  return `${path} L ${at(last).x} ${at(last).y}`;
}

function shiftOf(
  node: InternalNode | undefined,
  laid: RoutePoint,
): RoutePoint | null {
  if (node === undefined) return null;
  const absolute = node.internals.positionAbsolute;
  return { x: absolute.x - laid.x, y: absolute.y - laid.y };
}

// Where the line between two card centers leaves the first card's border.
function borderPoint(node: InternalNode, toward: InternalNode): RoutePoint {
  const box = (n: InternalNode): { cx: number; cy: number; hw: number; hh: number } => ({
    cx: n.internals.positionAbsolute.x + (n.measured.width ?? 0) / 2,
    cy: n.internals.positionAbsolute.y + (n.measured.height ?? 0) / 2,
    hw: (n.measured.width ?? 0) / 2,
    hh: (n.measured.height ?? 0) / 2,
  });
  const from = box(node);
  const to = box(toward);
  const dx = to.cx - from.cx;
  const dy = to.cy - from.cy;
  const scale = Math.min(
    from.hw / Math.max(Math.abs(dx), 1e-6),
    from.hh / Math.max(Math.abs(dy), 1e-6),
    1,
  );
  return { x: from.cx + dx * scale, y: from.cy + dy * scale };
}

export function TopologyEdge(props: EdgeProps<TopologyEdgeType>): ReactNode {
  const source = useInternalNode(props.source);
  const target = useInternalNode(props.target);
  const data = props.data;
  const sourceShift =
    data === undefined ? null : shiftOf(source, data.laid.source);
  const targetShift =
    data === undefined ? null : shiftOf(target, data.laid.target);
  const routed =
    data !== undefined &&
    sourceShift !== null &&
    targetShift !== null &&
    Math.abs(sourceShift.x - targetShift.x) < 0.5 &&
    Math.abs(sourceShift.y - targetShift.y) < 0.5;
  if (source === undefined || target === undefined) {
    throw new Error(`Atlas edge ${props.id} lost an endpoint node`);
  }
  const start = borderPoint(source, target);
  const end = borderPoint(target, source);
  let path: string;
  if (routed) {
    path = roundedPath(data.points, sourceShift);
  } else {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const len = Math.hypot(dx, dy);
    const tension = Math.min(len * 0.35, 80);
    const mx = (start.x + end.x) / 2;
    const my = (start.y + end.y) / 2;
    if (Math.abs(dx) > Math.abs(dy)) {
      path = `M ${start.x} ${start.y} C ${start.x + tension} ${start.y}, ${end.x - tension} ${end.y}, ${end.x} ${end.y}`;
    } else {
      path = `M ${start.x} ${start.y} Q ${mx} ${start.y}, ${mx} ${my} Q ${mx} ${end.y}, ${end.x} ${end.y}`;
    }
  }
  return (
    <BaseEdge
      id={props.id}
      path={path}
      markerEnd={props.markerEnd}
      style={props.style}
    />
  );
}
