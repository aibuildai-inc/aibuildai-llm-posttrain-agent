// A Search execution as a spatial scope: the cluster owns the space its
// composites, WorkUnits, and child Searches are laid out in. The surface
// itself is the containment channel — no folder metaphor, no tree row.
// The header carries the scope facts (family, uid, lifecycle, best metric,
// result and active counts); semantic zoom decides how loud they are
// (styles/atlas.css scales the header up at the run-overview tier).
import { NodeResizer, type NodeProps, type Node } from "@xyflow/react";
import { Focus, Waypoints } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "react-aria-components";
import type { SearchNodeData } from "../graph";
import { Ports } from "../ports";
import { StatusDot } from "../../components/StatusBadge";
import { fmtMetric } from "../../format";
import { ExecutionSpans } from "../../components/ExecutionSpans";

export function SearchClusterNode(
  props: NodeProps<Node<SearchNodeData, "search">>,
): ReactNode {
  const { summary, resultCount, doneCount, activeCount, failedCount, bestMetric, remainingS, totalCost, isSelectedResult } =
    props.data;
  return (
    <div
      className="search-cluster"
      data-status={summary.status_style_token}
      data-depth={props.data.depth}
      data-result={isSelectedResult || undefined}
    >
      <NodeResizer minWidth={120} minHeight={48} />
      <Ports direction={props.data.direction} />
      <div className="search-header">
        <Waypoints size={16} strokeWidth={1.75} aria-hidden />
        <span className="search-kind">{summary.family}</span>
        <span className="search-uid num">{summary.label}</span>
        <StatusDot token={summary.status_style_token} />
        <span className="search-status">{summary.status_word}</span>
        <span className="search-counters num">
          {resultCount > 0 && (
            <span className="search-counter">{resultCount} results</span>
          )}
          {doneCount > 0 && (
            <span className="search-counter" data-kind="done">
              {doneCount} done
            </span>
          )}
          {activeCount > 0 && (
            <span className="search-counter" data-kind="active">
              {activeCount} active
            </span>
          )}
          {failedCount > 0 && (
            <span className="search-counter" data-kind="failed">
              {failedCount} failed
            </span>
          )}
          {bestMetric !== null && (
            <span className="search-counter" data-kind="best">
              best {fmtMetric(bestMetric)}
            </span>
          )}
        </span>
        <span className="exec-spans num">
          <ExecutionSpans
            elapsedS={summary.elapsed_s}
            budgetS={summary.budget_s}
            remainingS={remainingS}
            cost={summary.cost}
            costOverride={totalCost}
            metric={summary.metric}
          />
        </span>
        <Button
          className="search-focus nodrag nopan"
          aria-label={`Focus search ${summary.label}`}
          onPress={() => props.data.onFocusScope(summary.path)}
        >
          <Focus size={14} strokeWidth={1.75} aria-hidden />
        </Button>
      </div>
    </div>
  );
}
