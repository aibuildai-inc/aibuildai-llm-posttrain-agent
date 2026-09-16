// The one execution-type icon channel. Type is drawn in neutral project
// ink; lifecycle color stays owned by the status channel. Icons are Lucide,
// imported individually, and deliberately non-filesystem: containment is an
// execution trace, never folders and files. The family mapping is shared
// with the Atlas WorkUnit nodes.
import {
  Activity,
  Bot,
  Component,
  Diamond,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import type { ExecutionSummary } from "../api/client";

export const FAMILY_ICON: Record<ExecutionSummary["family"], LucideIcon> = {
  search: Waypoints,
  composite: Diamond,
  agent: Bot,
  program: Activity,
  work_unit: Component,
  execution: Component,
};

export function executionIcon(summary: ExecutionSummary): LucideIcon {
  return FAMILY_ICON[summary.family];
}

// The one word that annotates an execution's name where the slot is one line
// wide: the Action method when this occurrence has one, else the family. An
// identity with several Action methods draws the same family word on every
// card, so the method is what tells its occurrences apart, and the icon beside
// it still carries the family. The Inspector title does NOT use this: that
// panel has room for both, so it keeps the family word and reads the method
// from the execution's own facts. A Search cluster has one Action method, so
// SearchClusterNode draws the family directly and does not come through here.
export function executionAnnotation(summary: ExecutionSummary): string {
  return summary.action_method ?? summary.family;
}

export function ExecutionIcon(props: {
  summary: ExecutionSummary;
  size?: number;
}): ReactNode {
  const Icon = executionIcon(props.summary);
  return (
    <Icon
      className="execution-icon"
      size={props.size ?? 15}
      strokeWidth={1.75}
      aria-hidden
    />
  );
}
