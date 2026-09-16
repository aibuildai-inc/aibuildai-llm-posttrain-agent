import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Group,
  Panel,
  Separator,
  usePanelRef,
  type LayoutChangedMeta,
} from "react-resizable-panels";
import {
  fetchCapabilities,
  fetchCsrf,
  fetchRun,
  postRunAction,
  RequestError,
} from "../../api/client";
import { AppHeader, RunFacts, type Liveness } from "../../app/AppHeader";
import { FrameContext } from "../../app/replay";
import { useRunId } from "../../app/runId";
import { findRun, useRuns } from "../../app/runs";
import { AtlasCanvas } from "../../atlas/AtlasCanvas";
import { InspectorShell } from "../../inspector/InspectorShell";
import { useMotionTransition } from "../../motionPresets";
import { RunSelector } from "../../navigation/RunSelector";
import { WorkspaceShell } from "../../workspace/WorkspaceShell";
import { fmtHhmmss } from "../../format";
import { LifecycleActions } from "./LifecycleActions";
import { TimelineBar } from "./ReplayBar";
import { ResourceDock } from "./ResourceDock";

// The Workspace panel's tree-only width, and how much it grows, once,
// when the first file opens from the tree-only state: an initial
// presentation aid, never a contract, and never applied again after the
// user has resized anything.
const TREE_ONLY_PX = 320;
const PREFERRED_PREVIEW_PX = 480;

export function RunPage(): ReactNode {
  const runId = useRunId();
  // Selection, Search-scope focus, and the open workspace file live in the
  // URL as three independent states, so refresh, back/forward, and a
  // pasted link restore the same view with no browser session state on
  // the Python side.
  const [params, setParams] = useSearchParams();
  const selectedPath = params.get("execution");
  const scopePath = params.get("scope");
  const file = params.get("file");
  const transition = useMotionTransition({ duration: 0.22 });
  const runsQuery = useRuns();
  const summary = findRun(runsQuery.data, runId);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const runQuery = useQuery({
    queryKey: ["run", runId, playhead],
    queryFn: () => fetchRun(runId, playhead ?? undefined),
    placeholderData: (previous) => previous?.run_id === runId ? previous : undefined,
    refetchInterval: (query) =>
      query.state.data?.result_recorded === true && playhead === null
        ? false
        : summary?.active === true
          ? 1000
          : 5000,
    retry: false,
  });
  const run = runQuery.data;
  const headS = useRef<number | null>(null);
  const physicsAt = useRef(performance.now());
  useEffect(() => {
    headS.current = run?.head_elapsed_s ?? null;
  }, [run?.head_elapsed_s]);
  useEffect(() => {
    physicsAt.current = performance.now();
    if (!playing) return;
    const timer = window.setInterval(() => {
      const now = performance.now();
      const dt = (now - physicsAt.current) / 1000;
      physicsAt.current = now;
      const head = headS.current;
      if (head === null) return;
      setPlayhead((current) => {
        if (current === null) return null;
        const next = Math.min(current + speed * dt, head);
        return next >= head ? null : next;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [playing, speed]);
  useEffect(() => {
    if (run?.result_recorded === true && playhead === null) setPlaying(false);
  }, [run?.result_recorded, playhead]);
  const queryClient = useQueryClient();
  // ---- Lifecycle actions (pause / resume) ----
  const capabilitiesQuery = useQuery({
    queryKey: ["capabilities", runId],
    queryFn: () => fetchCapabilities(runId),
    refetchInterval: 2000,
    retry: false,
  });
  const [failureDismissed, setFailureDismissed] = useState(false);
  const capabilities = capabilitiesQuery.data;
  const failure = failureDismissed ? null : capabilities?.failure ?? null;
  const act = (kind: "pause" | "resume"): void => {
    if (kind === "pause" && !window.confirm("Pause this run? Completed progress is preserved, but in-flight work may restart after Resume.")) return;
    void fetchCsrf()
      .then((csrf) => postRunAction(runId, kind, csrf))
      .then(() => capabilitiesQuery.refetch())
      .then(() => {
        setFailureDismissed(false);
        void queryClient.invalidateQueries({ queryKey: ["runs"] });
        void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      })
      .catch((error: unknown) => {
        window.alert(error instanceof Error ? error.message : String(error));
      });
  };
  const shownRun = run;
  const followingHead = playhead === null;
  const frame = run === undefined ? null : { revision: run.frame_revision, historical: !followingHead };
  const stale = run !== undefined && !run.result_recorded && summary?.active === true && followingHead && run.view_age_s > 5;
  // The service answers 404 only for a run id the durable index does not
  // know. A run it knows but cannot serve yet answers 503, and the run list
  // says whether its projection is restoring or unavailable.
  const unknown =
    runQuery.isError &&
    runQuery.error instanceof RequestError &&
    runQuery.error.status === 404 &&
    runsQuery.data !== undefined &&
    summary === undefined;
  const liveness: Liveness =
    summary?.availability === "unavailable" || summary?.incompatible === true
      ? "unavailable"
      : summary?.availability === "restoring"
        ? "restoring"
        : runQuery.isError && !unknown
          ? "disconnected"
          : stale
            ? "stale"
            : summary?.active === true
              ? "live"
              : "inactive";
  const summaries = new Map((shownRun?.executions ?? []).map((s) => [s.path, s]));
  const setParam = (name: string, value: string | null): void => {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value === null) next.delete(name);
      else next.set(name, value);
      return next;
    });
  };
  const select = (path: string | null): void => setParam("execution", path);
  const focusScope = (path: string | null): void => setParam("scope", path);
  const selectFile = (path: string | null): void => setParam("file", path);
  const [reveal, setReveal] = useState<{ path: string; nonce: number } | null>(null);
  // The Workspace closes from its header like the Inspector does, and
  // opens again from the header or from a reveal.
  const [workspaceOpen, setWorkspaceOpen] = useState(true);

  // Width ownership: the user wins. The outer Workspace panel grows once
  // on the first file open from the tree-only state and may shrink back
  // when the file closes, but only while no separator has been moved by
  // hand; after any user resize the layout is theirs and nothing here
  // touches it again.
  const workspacePanel = usePanelRef();
  const userOwned = useRef(false);
  const fileOpen = file !== null;
  // A deep-linked ?file=... gets the same one-time initial expansion as
  // a clicked one, declaratively: the panel MOUNTS at the expanded width
  // (see defaultSize below), because an imperative resize in the mount
  // pass runs before the group has a layout for the new panel. These
  // refs start from the mount-time state so the effect only ever handles
  // real open/close transitions, which happen with the layout in place.
  const initialFileOpen = useRef(fileOpen);
  const wasOpen = useRef(initialFileOpen.current);
  const treeOnlyWidth = useRef<number | null>(initialFileOpen.current ? TREE_ONLY_PX : null);
  useEffect(() => {
    if (wasOpen.current === fileOpen) return;
    wasOpen.current = fileOpen;
    const panel = workspacePanel.current;
    if (panel === null || userOwned.current) return;
    if (fileOpen) {
      treeOnlyWidth.current = panel.getSize().inPixels;
      panel.resize(treeOnlyWidth.current + PREFERRED_PREVIEW_PX);
    } else if (treeOnlyWidth.current !== null) {
      panel.resize(treeOnlyWidth.current);
    }
  }, [fileOpen, workspacePanel]);
  const onLayoutChanged = (_layout: unknown, meta: LayoutChangedMeta): void => {
    if (meta.isUserInteraction) userOwned.current = true;
  };

  const navigate = useNavigate();
  const headerSelector = (
    <RunSelector
      runs={runsQuery.data?.runs ?? []}
      selectedRunId={runId}
      onSelect={(next) => void navigate(`/run/${next}`)}
    />
  );
  const blocked = unknown || run === undefined;
  const atlas = blocked ? (
    <div className="atlas-backdrop atlas-gone">
      <div className="atlas-gone-strip">{headerSelector}</div>
      <p className="quiet atlas-waiting">
        {unknown
          ? "this run id is not in the run index — pick a run above"
          : liveness === "unavailable"
              ? (summary?.diagnostic ?? "this run cannot be shown")
              : liveness === "restoring"
                ? "restoring the run from its journal…"
                : "waiting for run state…"}
      </p>
    </div>
  ) : (
    <AtlasCanvas
      run={shownRun ?? run}
      selectedPath={selectedPath}
      scopePath={scopePath}
      onSelect={select}
      onFocusScope={focusScope}
      navigation={null}
    />
  );
  const projectionReady = !runQuery.isError && summary?.availability !== "unavailable";
  const inspector =
    selectedPath === null || run === undefined || unknown || !projectionReady ? null : (
      <InspectorShell
        path={selectedPath}
        runLive={!run.result_recorded && liveness === "live" && followingHead}
        metricDirection={run.metric_direction}
        summaries={summaries}
        onSelect={select}
        onReveal={(path) => {
          setWorkspaceOpen(true);
          setReveal((prev) => ({ path, nonce: (prev?.nonce ?? 0) + 1 }));
        }}
      />
    );
  const workspace =
    run === undefined || unknown || !workspaceOpen || !projectionReady ? null : (
      <WorkspaceShell
        runLive={summary?.active === true}
        file={file}
        onSelectFile={selectFile}
        reveal={reveal}
        onClose={() => setWorkspaceOpen(false)}
      />
    );
  return (
    <div className="app-shell">
      <AppHeader>
        {unknown ? (
          <span className="quiet">unknown run <span className="num">{runId}</span></span>
        ) : (
          <RunFacts
            run={shownRun}
            badgeRun={run}
            liveness={liveness}
            diagnostic={summary?.diagnostic ?? null}
            runSelector={headerSelector}
            timelineClock={
              run !== undefined && playhead !== null
                ? `${fmtHhmmss(playhead)} / ${fmtHhmmss(run.head_elapsed_s)}`
                : null
            }
            onOpenWorkspace={workspaceOpen || run === undefined ? null : () => setWorkspaceOpen(true)}
            actions={
              <LifecycleActions
                capabilities={capabilitiesQuery.data}
                onPause={() => act("pause")}
                onResume={() => act("resume")}
                failure={failure}
                onDismissFailure={() => setFailureDismissed(true)}
                onRetry={() => {
                  if (failure !== null) act(failure.kind);
                }}
              />
            }
          />
        )}
      </AppHeader>
      <main className="atlas-region" aria-label="Run workbench">
        <FrameContext.Provider value={frame}>
        {/* Inspector | Atlas | Workspace. No panel protects another with
            a minimum or maximum: every width is the user's to set. */}
        <Group
          orientation="horizontal"
          id="run-workbench"
          className="atlas-split"
          onLayoutChanged={onLayoutChanged}
        >
          {inspector !== null && (
            <>
              <Panel id="inspector" defaultSize={430}>
                <motion.div
                  className="inspector-panel"
                  initial={{ x: -32, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  transition={transition}
                >
                  {inspector}
                </motion.div>
              </Panel>
              <Separator className="inspector-resize" aria-label="Resize inspector" />
            </>
          )}
          <Panel id="atlas">
            <div className="atlas-host">{atlas}</div>
          </Panel>
          {workspace !== null && (
            <>
              <Separator className="inspector-resize" aria-label="Resize workspace" />
              <Panel
                id="workspace"
                defaultSize={TREE_ONLY_PX + (initialFileOpen.current ? PREFERRED_PREVIEW_PX : 0)}
                panelRef={workspacePanel}
              >
                {workspace}
              </Panel>
            </>
          )}
        </Group>
        </FrameContext.Provider>
      </main>
      <ResourceDock runId={runId} framePositionS={playhead} runLive={run !== undefined && !run.result_recorded && liveness === "live"} />
      {run !== undefined && (
        <TimelineBar
          headS={run.head_elapsed_s}
          positionS={playhead ?? run.head_elapsed_s}
          playing={playing}
          speed={speed}
          onPosition={(position) => setPlayhead(position >= run.head_elapsed_s ? null : position)}
          onPlaying={(next) => {
            if (!next && playhead === null) setPlayhead(run.head_elapsed_s);
            setPlaying(next);
          }}
          onSpeed={setSpeed}
        />
      )}
    </div>
  );
}
