// The Inspector: appears only after a selection, on the left of the
// Atlas (see App). The shell
// owns the data fetching, the header (family icon, label, status, close)
// and the crossfade between selections. Search, Composite, and WorkUnit
// each compose their own sections. No selection, no Inspector.
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { PanelRightOpen, X } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "react-aria-components";
import {
  fetchExecution,
  fetchTranscript,
  type ExecutionSummary,
  type MetricDirection,
} from "../api/client";
import { ExecutionIcon } from "../components/ExecutionIcon";
import { StatusBadge } from "../components/StatusBadge";
import { useMotionTransition } from "../motionPresets";
import { useFrame } from "../app/replay";
import { useRunId } from "../app/runId";
import { CompositeInspector } from "./CompositeInspector";
import { SearchInspector } from "./SearchInspector";
import { WorkUnitInspector } from "./WorkUnitInspector";

export function InspectorShell(props: {
  path: string;
  runLive: boolean;
  metricDirection: MetricDirection;
  summaries: Map<string, ExecutionSummary>;
  onSelect: (path: string | null) => void;
  // Reveal this execution's own directory in the Workspace tree; the
  // path comes from the backend (ExecutionDetailResponse.workspace_path).
  onReveal: (workspacePath: string) => void;
}): ReactNode {
  const { path, runLive } = props;
  const runId = useRunId();
  const frame = useFrame();
  const summary = props.summaries.get(path);
  const detailQuery = useQuery({
    queryKey: ["execution", runId, frame?.revision, path],
    queryFn: () => fetchExecution(runId, path, frame?.revision),
    enabled: summary !== undefined,
    refetchInterval: frame === null && runLive ? 2000 : false,
    retry: false,
  });
  const transcriptAvailable = summary?.transcript_available === true;
  const transcriptQuery = useQuery({
    queryKey: ["transcript", runId, frame?.revision, path],
    queryFn: () => fetchTranscript(runId, path, frame?.revision),
    enabled: transcriptAvailable,
    refetchInterval: (query) =>
      frame === null && runLive && query.state.data?.terminal !== true ? 2000 : false,
    retry: false,
  });
  const transition = useMotionTransition({ duration: 0.18 });
  let body;
  if (summary === undefined && frame !== null) {
    // The selection outlives the frame: this execution had not been
    // created at the current replay position. Keep it selected; when the
    // playhead passes its creation, the frame's summaries include it
    // again and the Inspector reappears by itself.
    body = (
      <p className="quiet inspector-missing">
        Not available at this replay position — this execution had not been
        created yet. It will appear when the playhead passes its creation.
      </p>
    );
  } else if (summary === undefined) {
    body = (
      <p className="quiet inspector-missing">unknown execution path: {path}</p>
    );
  } else if (detailQuery.isPending) {
    body = <p className="quiet inspector-missing">loading execution…</p>;
  } else if (detailQuery.isError) {
    body = (
      <p className="quiet inspector-missing">execution details unavailable</p>
    );
  } else {
    const detail = detailQuery.data;
    body =
      summary.family === "search" ? (
        <SearchInspector
          detail={detail}
          metricDirection={props.metricDirection}
          summaries={props.summaries}
          onSelect={props.onSelect}
        />
      ) : summary.family === "composite" ? (
        <CompositeInspector
          detail={detail}
          summaries={props.summaries}
          onSelect={props.onSelect}
        />
      ) : (
        <WorkUnitInspector
          detail={detail}
          transcript={transcriptQuery.data}
          transcriptPending={transcriptAvailable && transcriptQuery.isPending}
          transcriptError={transcriptAvailable && transcriptQuery.isError}
          runLive={runLive}
          summaries={props.summaries}
          onSelect={props.onSelect}
        />
      );
  }
  return (
    <div className="inspector">
      <header className="inspector-header">
        {summary !== undefined && <ExecutionIcon summary={summary} size={16} />}
        <div className="inspector-title">
          <span className="inspector-label num">{summary?.label ?? path}</span>
          <span className="inspector-family">
            {summary === undefined ? "unknown execution" : summary.family}
          </span>
        </div>
        {summary !== undefined && (
          <StatusBadge
            word={summary.status_word}
            token={summary.status_style_token}
          />
        )}
        {detailQuery.data !== undefined && (
          <Button
            className="inspector-close"
            aria-label="Reveal in workspace"
            onPress={() => props.onReveal(detailQuery.data.workspace_path)}
          >
            <PanelRightOpen size={15} strokeWidth={1.75} aria-hidden />
          </Button>
        )}
        <Button
          className="inspector-close"
          aria-label="Close inspector"
          onPress={() => props.onSelect(null)}
        >
          <X size={15} strokeWidth={1.75} aria-hidden />
        </Button>
      </header>
      {/* Changing the selection crossfades the body; poll refreshes keep
          the same key and never animate. */}
      <motion.div
        key={path}
        className="inspector-body"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={transition}
      >
        {body}
      </motion.div>
    </div>
  );
}
