// A Composite execution card. It shows a metric only when its owning Search
// publishes it as a result. Lifecycle and owned WorkUnit phases stay separate.
// The selected result keeps its crown; the selected item keeps the active outline.
import { NodeResizer, type NodeProps, type Node } from "@xyflow/react";
import { Crown, Diamond } from "lucide-react";
import type { ReactNode } from "react";
import type { CompositeNodeData } from "../graph";
import { Ports } from "../ports";
import { StatusDot } from "../../components/StatusBadge";
import { fmtCost, fmtHhmmss, fmtMetric } from "../../format";

export function CompositeCardNode(
  props: NodeProps<Node<CompositeNodeData, "composite">>,
): ReactNode {
  const { summary, isSelectedResult, metricName, phases, direction } = props.data;
  return (
    <div
      className="composite-card"
      data-status={summary.status_style_token}
      data-result={isSelectedResult || undefined}
    >
      <NodeResizer minWidth={120} minHeight={48} />
      <Ports direction={direction} />
      <div className="composite-content">
      <div className="composite-top">
        <Diamond size={13} strokeWidth={1.75} aria-hidden />
        <span className="composite-uid num">{summary.label}</span>
        {isSelectedResult && <Crown size={13} strokeWidth={1.75} aria-hidden className="composite-crown" />}
        <StatusDot token={summary.status_style_token} />
        <span className="execution-status">{summary.status_word}</span>
      </div>
      <div className="composite-hero">
        {summary.metric !== null ? (
          <>
            <span className="composite-metric num">
              {fmtMetric(summary.metric)}
            </span>
            <span className="composite-metric-name">{metricName}</span>
          </>
        ) : (
          <span className="composite-metric-pending">
            {summary.status_word}
          </span>
        )}
      </div>
      <div className="composite-foot">
        <span className="composite-phases" aria-hidden>
          {phases.map((phase) => (
            <span
              key={phase.key}
              className="phase-tick"
              data-token={phase.token}
            />
          ))}
        </span>
        <span className="composite-spans num">
          {summary.elapsed_s !== null && fmtHhmmss(summary.elapsed_s)}
          {summary.cost !== null && summary.cost > 0 && (
            <> · {fmtCost(summary.cost)}</>
          )}
        </span>
      </div>
      </div>
    </div>
  );
}
