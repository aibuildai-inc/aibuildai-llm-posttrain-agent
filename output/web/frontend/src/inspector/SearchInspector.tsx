// The Search scope Inspector: the scope's spans, its best result, the
// composite ranking as a sortable structured table, the scope's subtree
// as an outline tree, then input/output and any owned failure.
import type { ColumnDef } from "@tanstack/react-table";
import type { ReactNode } from "react";
import { Button } from "react-aria-components";
import type {
  ExecutionDetailResponse,
  ExecutionSummary,
  MetricDirection,
} from "../api/client";
import { StatusDot } from "../components/StatusBadge";
import { fmtCost, fmtHhmmss, fmtMetric } from "../format";
import { bestMetric } from "../metric";
import { DataTable } from "../tables/DataTable";
import { Outline } from "./Outline";
import {
  Facts,
  FailureBlock,
  MetricHero,
  RawBlock,
  Section,
  SpanFacts,
} from "./sections";

// One scored execution in this Search's own scope. A scored execution says so
// on its own summary, so a row IS that execution rather than a separate result
// record pointing back at it.
type SearchResultRow = { summary: ExecutionSummary; score: number };

function resultColumns(
  onSelect: (path: string | null) => void,
): ColumnDef<SearchResultRow, unknown>[] {
  return [
    {
      id: "result",
      header: "result",
      cell: ({ row }) => (
        <Button
          className="composite-link cell-label num"
          onPress={() => onSelect(row.original.summary.path)}
        >
          {row.original.summary.label}
        </Button>
      ),
      size: 150,
    },
    {
      id: "status",
      header: "status",
      accessorFn: (row: SearchResultRow) => row.summary.status_word,
      cell: ({ row }) => (
        <span className="cell-status">
          <StatusDot token={row.original.summary.status_style_token} />
          {row.original.summary.status_word}
        </span>
      ),
      size: 110,
    },
    {
      id: "metric",
      header: "metric",
      accessorFn: (row: SearchResultRow) => row.score,
      cell: ({ row }) => (
        <span className="num cell-metric">{fmtMetric(row.original.score)}</span>
      ),
      size: 90,
    },
    {
      id: "cost",
      header: "cost",
      accessorFn: (row: SearchResultRow) => row.summary.cost ?? undefined,
      sortUndefined: "last",
      cell: ({ row }) => (
        <span className="num">
          {row.original.summary.cost != null && row.original.summary.cost > 0
            ? fmtCost(row.original.summary.cost)
            : "—"}
        </span>
      ),
      size: 80,
    },
    {
      id: "elapsed",
      header: "elapsed",
      accessorFn: (row: SearchResultRow) =>
        row.summary.elapsed_s ?? undefined,
      sortUndefined: "last",
      cell: ({ row }) => (
        <span className="num">
          {row.original.summary.elapsed_s != null
            ? fmtHhmmss(row.original.summary.elapsed_s)
            : "—"}
        </span>
      ),
      size: 90,
    },
  ];
}


export function SearchInspector(props: {
  detail: ExecutionDetailResponse;
  metricDirection: MetricDirection;
  summaries: Map<string, ExecutionSummary>;
  onSelect: (path: string | null) => void;
}): ReactNode {
  const { detail } = props;
  const children = detail.child_paths
    .map((path) => props.summaries.get(path))
    .filter((summary): summary is ExecutionSummary => summary !== undefined);
  const results: SearchResultRow[] = children
    .filter((summary) => summary.score !== null)
    .map((summary) => ({ summary, score: summary.score!.score }));
  const metrics = results.map((row) => row.score);
  return (
    <>
      <SpanFacts summary={detail.summary} />
      <MetricHero
        value={bestMetric(metrics, props.metricDirection)}
        name="best in scope"
        fallback="no scored result yet"
      />
      {results.length > 0 && (
        <Section title={`results · ${results.length}`}>
          <DataTable
            columns={resultColumns(props.onSelect)}
            data={results}
            initialSorting={[
              { id: "metric", desc: props.metricDirection === "max" },
            ]}
          />
        </Section>
      )}
      {children.length > 0 && (
        <Section title="outline">
          <Outline
            rootPath={detail.summary.path}
            summaries={props.summaries}
            onSelect={props.onSelect}
          />
        </Section>
      )}
      <Facts facts={detail.facts} />
      {detail.input_summary !== null && (
        <Section title="input">
          <RawBlock text={detail.input_summary} />
        </Section>
      )}
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
