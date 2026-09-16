import type { ReactNode } from "react";
import { fmtCost, fmtHhmmss, fmtMetric, progressTier } from "../format";

export function ExecutionSpans(props: {
  elapsedS: number | null;
  budgetS?: number | null;
  remainingS?: number | null;
  cost: number | null;
  costOverride?: number | null;
  metric?: number | null;
  model?: string | null;
  showModel?: boolean;
}): ReactNode {
  const cost = props.costOverride ?? props.cost;
  const elapsed = props.elapsedS;
  return (
    <>
      {elapsed !== null && props.budgetS != null && props.budgetS > 0 ? (
        <span className="exec-progress">
          <span>{fmtHhmmss(elapsed)}</span>
          <span className="exec-progress-track">
            <span
              className="exec-progress-fill"
              data-tier={progressTier((elapsed / props.budgetS) * 100)}
              style={{ width: `${Math.min(100, (elapsed / props.budgetS) * 100)}%` }}
            />
          </span>
          <span>{fmtHhmmss(props.budgetS)}</span>
        </span>
      ) : elapsed !== null && props.remainingS != null ? (
        <span>{fmtHhmmss(elapsed)} / {fmtHhmmss(elapsed + props.remainingS)}</span>
      ) : elapsed !== null ? (
        <span>{fmtHhmmss(elapsed)}</span>
      ) : null}
      {cost !== null && cost > 0 && <span>{fmtCost(cost)}</span>}
      {props.metric != null && (
        <span>{fmtMetric(props.metric)}</span>
      )}
      {props.showModel && props.model != null && (
        <span className="exec-model">{props.model}</span>
      )}
    </>
  );
}
