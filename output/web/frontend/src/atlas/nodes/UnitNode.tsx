// A WorkUnit as a compact execution entity. The stable family decides its
// icon, while the
// lifecycle status stays its own color channel, exactly like everywhere
// else in the product. A unit that owns children is a compound: its own facts
// sit in a fixed-height header block and the children lay out in the
// space below it, which ELK reserves with the same height (layout.ts
// UNIT_PADDING). Semantic zoom hides the second line and then the label
// as the viewport moves to the run-overview tier.
import { NodeResizer, type NodeProps, type Node } from "@xyflow/react";
import type { ReactNode } from "react";
import type { UnitNodeData } from "../graph";
import { FAMILY_ICON, executionAnnotation } from "../../components/ExecutionIcon";
import { StatusDot } from "../../components/StatusBadge";
import { ExecutionSpans } from "../../components/ExecutionSpans";
import { Ports } from "../ports";

export function UnitNode(
  props: NodeProps<Node<UnitNodeData, "unit">>,
): ReactNode {
  const { summary, family, compound, modelsRouted, direction, isSelectedResult } = props.data;
  const Icon = FAMILY_ICON[family];
  return (
    <div
      className="unit-node"
      data-family={family}
      data-status={summary.status_style_token}
      data-compound={compound || undefined}
      data-result={isSelectedResult || undefined}
    >
      <NodeResizer minWidth={120} minHeight={48} />
      <Ports direction={direction} />
      <div className="unit-content">
        <div className="unit-top">
          <Icon size={13} strokeWidth={1.75} aria-hidden />
          <span className="unit-title">
            <span className="unit-name">{summary.label}</span>
            <span className="unit-kind">{executionAnnotation(summary)}</span>
          </span>
          <StatusDot token={summary.status_style_token} />
          <span className="unit-status">{summary.status_word}</span>
        </div>
        <div className="exec-spans num">
          <ExecutionSpans
            elapsedS={summary.elapsed_s}
            budgetS={summary.budget_s}
            cost={summary.cost}
            metric={summary.metric}
            model={summary.model}
            showModel={modelsRouted}
          />
        </div>
      </div>
    </div>
  );
}
