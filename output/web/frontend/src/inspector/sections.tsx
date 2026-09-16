// Shared composition pieces for the family Inspectors: a titled section,
// the metric hero, designed fact rows, execution child rows in the
// Atlas's own language, raw input/output surfaces, and the failure
// surface. Each family Inspector composes THESE into its own shape —
// there is no generic facts-table page.
import type { ColumnDef } from "@tanstack/react-table";
import { TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "react-aria-components";
import type { ExecutionSummary, FactRow, MetricComponent } from "../api/client";
import { executionAnnotation } from "../components/ExecutionIcon";
import { ExecutionSpans } from "../components/ExecutionSpans";
import { StatusDot } from "../components/StatusBadge";
import { fmtCost, fmtHhmmss, fmtMetric } from "../format";
import { DataTable } from "../tables/DataTable";

const METRIC_COMPONENT_COLUMNS: ColumnDef<MetricComponent, unknown>[] = [
  {
    id: "component",
    header: "component",
    accessorFn: (component: MetricComponent) => component.name,
    cell: ({ row }) => <span className="cell-label">{row.original.name}</span>,
  },
  {
    id: "value",
    header: "value",
    accessorFn: (component: MetricComponent) => component.value,
    cell: ({ row }) => (
      <span className="num cell-metric">{fmtMetric(row.original.value)}</span>
    ),
    size: 110,
  },
];

export function Section(props: {
  title: string;
  children: ReactNode;
}): ReactNode {
  return (
    <section className="inspector-section">
      <h3 className="inspector-section-title">{props.title}</h3>
      {props.children}
    </section>
  );
}

export function MetricHero(props: {
  value: number | null;
  name: string;
  fallback: string;
}): ReactNode {
  return (
    <div className="inspector-hero">
      {props.value !== null ? (
        <>
          <span className="inspector-hero-value num">
            {fmtMetric(props.value)}
          </span>
          <span className="inspector-hero-name">{props.name}</span>
        </>
      ) : (
        <span className="inspector-hero-fallback">{props.fallback}</span>
      )}
    </div>
  );
}

export function MetricComponents(props: {
  components: MetricComponent[];
}): ReactNode {
  if (props.components.length === 0) return null;
  return (
    <Section title="metric components">
      <DataTable columns={METRIC_COMPONENT_COLUMNS} data={props.components} />
    </Section>
  );
}

export function SpanFacts(props: { summary: ExecutionSummary }): ReactNode {
  const { summary } = props;
  return (
    <div className="inspector-spans num">
      <span className="inspector-span">
        <StatusDot token={summary.status_style_token} />
        {summary.status_word}
      </span>
      <ExecutionSpans
        elapsedS={summary.elapsed_s}
        budgetS={summary.budget_s}
        cost={summary.cost}
        model={summary.model}
        showModel
      />
    </div>
  );
}

export function Facts(props: { facts: FactRow[] }): ReactNode {
  if (props.facts.length === 0) return null;
  return (
    <dl className="inspector-facts">
      {props.facts.map((fact) => (
        <div key={fact.label} className="inspector-fact">
          <dt>{fact.label}</dt>
          <dd>{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ChildRows(props: {
  paths: string[];
  summaries: Map<string, ExecutionSummary>;
  onSelect: (path: string) => void;
}): ReactNode {
  const rows = props.paths
    .map((path) => props.summaries.get(path))
    .filter((summary): summary is ExecutionSummary => summary !== undefined);
  if (rows.length === 0) return null;
  return (
    <div className="inspector-children">
      {rows.map((child) => (
        <Button
          key={child.path}
          className="inspector-child"
          onPress={() => props.onSelect(child.path)}
        >
          <span className="inspector-child-word">
            {executionAnnotation(child)}
          </span>
          <span className="inspector-child-label num">{child.label}</span>
          <span className="inspector-child-meta num">
            {child.metric !== null
              ? fmtMetric(child.metric)
              : child.cost !== null && child.cost > 0
                ? fmtCost(child.cost)
                : child.elapsed_s !== null
                  ? fmtHhmmss(child.elapsed_s)
                  : ""}
          </span>
          <StatusDot token={child.status_style_token} />
        </Button>
      ))}
    </div>
  );
}

// The executions one selection consumes (or is consumed by), as rows in
// the Atlas's own language, each selectable. ``paths`` is the recorded
// direct relation; the rows keep its declared order.
export function RelationRows(props: {
  paths: string[];
  summaries: Map<string, ExecutionSummary>;
  onSelect: (path: string) => void;
}): ReactNode {
  return (
    <ChildRows
      paths={props.paths}
      summaries={props.summaries}
      onSelect={props.onSelect}
    />
  );
}

export function RawBlock(props: {
  text: string;
  failed?: boolean;
}): ReactNode {
  // Input and output can run to thousands of lines, so they start folded
  // and announce their size; one click opens the raw text.
  const lines = props.text.split("\n").length;
  return (
    <details className="inspector-raw-fold">
      <summary>
        <span className="inspector-raw-word">raw</span>
        <span className="inspector-raw-size num">{lines} lines</span>
      </summary>
      <pre className={`inspector-raw${props.failed ? " inspector-raw-failed" : ""}`}>
        {props.text}
      </pre>
    </details>
  );
}

export function FailureBlock(props: { text: string }): ReactNode {
  return (
    <div className="inspector-failure">
      <div className="inspector-failure-header">
        <TriangleAlert size={14} strokeWidth={1.75} aria-hidden />
        failure
      </div>
      <pre className="inspector-failure-text">{props.text}</pre>
    </div>
  );
}
