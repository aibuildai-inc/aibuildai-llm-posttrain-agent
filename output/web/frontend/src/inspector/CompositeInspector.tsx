// The Composite Inspector: the metric is the hero fact, then the
// lifecycle spans, lineage (the composites among its direct
// upstream, selectable), the other executions it consumes, the WorkUnit
// execution sequence, remaining composite facts (depth, metric
// components), and output/failure.
import type { ReactNode } from "react";
import type {
  ExecutionDetailResponse,
  ExecutionSummary,
} from "../api/client";
import { Button } from "react-aria-components";
import {
  ChildRows,
  Facts,
  FailureBlock,
  MetricHero,
  MetricComponents,
  RawBlock,
  RelationRows,
  Section,
  SpanFacts,
} from "./sections";

export function CompositeInspector(props: {
  detail: ExecutionDetailResponse;
  summaries: Map<string, ExecutionSummary>;
  onSelect: (path: string | null) => void;
}): ReactNode {
  const { detail } = props;
  const summary = detail.summary;
  const upstream = summary.upstream_actions.map((path) => {
    const producer = props.summaries.get(path);
    if (producer === undefined) {
      throw new Error(`${summary.path} has missing upstream ${path}`);
    }
    return producer;
  });
  // Composite lineage is the composite-to-composite subset of the one
  // recorded relation; the rest of the upstream is shown as producers.
  const parents = upstream.filter((producer) => producer.family === "composite");
  const producers = upstream.filter((producer) => producer.family !== "composite");
  return (
    <>
      <MetricHero
        value={summary.metric}
        name={summary.status === "failed" ? "no score" : "score"}
        fallback={summary.status_word.toLowerCase()}
      />
      <SpanFacts summary={summary} />
      {parents.length > 0 && (
        <Section title="lineage">
          <div className="inspector-lineage">
            {parents.map((parent) => (
              <Button
                key={parent.path}
                className="inspector-lineage-link num"
                onPress={() => props.onSelect(parent.path)}
              >
                {parent.label}
              </Button>
            ))}
            <span className="inspector-lineage-arrow num">
              → {summary.label}
            </span>
          </div>
        </Section>
      )}
      {producers.length > 0 && (
        <Section title="upstream">
          <RelationRows
            paths={producers.map((producer) => producer.path)}
            summaries={props.summaries}
            onSelect={props.onSelect}
          />
        </Section>
      )}
      {detail.child_paths.length > 0 && (
        <Section title="execution sequence">
          <ChildRows
            paths={detail.child_paths}
            summaries={props.summaries}
            onSelect={props.onSelect}
          />
        </Section>
      )}
      <MetricComponents components={detail.metric_components} />
      <Facts facts={detail.facts} />
      {detail.output_summary !== null && (
        <Section title="output">
          <RawBlock text={detail.output_summary} />
        </Section>
      )}
      {detail.failure_summary !== null && (
        <FailureBlock text={detail.failure_summary} />
      )}
    </>
  );
}
