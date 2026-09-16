// One Inspector for Agent, Program, and fallback WorkUnit families. Each
// section appears only when its typed recorded fact exists.
import { lazy, Suspense, type ReactNode } from "react";
import type {
  ExecutionDetailResponse,
  ExecutionSummary,
  TranscriptResponse,
} from "../api/client";
import { RecordedMetrics } from "./RecordedMetrics";
import {
  Facts,
  RelationRows,
  FailureBlock,
  MetricComponents,
  MetricHero,
  RawBlock,
  Section,
  SpanFacts,
} from "./sections";

const TranscriptView = lazy(() =>
  import("../components/TranscriptView").then((module) => ({
    default: module.TranscriptView,
  })),
);

function TranscriptSection(props: {
  pending: boolean;
  error: boolean;
  transcript: TranscriptResponse | undefined;
}): ReactNode {
  let content;
  if (props.pending) {
    content = <p className="quiet">Loading transcript…</p>;
  } else if (props.error) {
    content = <p className="quiet">Transcript unavailable</p>;
  } else if (
    props.transcript === undefined ||
    (props.transcript.turns.length === 0 && props.transcript.header_markdown === "")
  ) {
    content = <p className="quiet">This WorkUnit has no transcript content yet</p>;
  } else {
    content = (
      <Suspense fallback={<p className="quiet">Loading transcript…</p>}>
        <TranscriptView transcript={props.transcript} />
      </Suspense>
    );
  }
  return <Section title="transcript">{content}</Section>;
}

export function WorkUnitInspector(props: {
  detail: ExecutionDetailResponse;
  summaries: Map<string, ExecutionSummary>;
  onSelect: (path: string | null) => void;
  transcript: TranscriptResponse | undefined;
  transcriptPending: boolean;
  transcriptError: boolean;
  runLive: boolean;
}): ReactNode {
  const { detail } = props;
  const summary = detail.summary;
  return (
    <>
      <SpanFacts summary={summary} />
      {summary.metric !== null && (
        <MetricHero value={summary.metric} name="metric" fallback="" />
      )}
      <MetricComponents components={detail.metric_components} />
      {summary.upstream_actions.length > 0 && (
        <Section title="upstream">
          <RelationRows
            paths={summary.upstream_actions}
            summaries={props.summaries}
            onSelect={props.onSelect}
          />
        </Section>
      )}
      <Facts facts={detail.facts} />
      {summary.family !== "execution" && (
        <RecordedMetrics
          path={summary.path}
          live={props.runLive && summary.status === "running"}
        />
      )}
      {summary.transcript_available && (
        <TranscriptSection
          pending={props.transcriptPending}
          error={props.transcriptError}
          transcript={props.transcript}
        />
      )}
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
