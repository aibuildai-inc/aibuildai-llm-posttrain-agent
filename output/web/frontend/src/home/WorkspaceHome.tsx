// The Workspace home page: an intentionally empty Atlas. The same warm
// paper and dotted grid the run Atlas draws, without a React Flow canvas,
// and one centred anchor of copy: what this is, what to do, and how many
// runs there are. It never auto-selects or redirects, even with one run.
import type { ReactNode } from "react";
import { useRuns } from "../app/runs";
import { WorkspaceNavigation } from "../navigation/WorkspaceNavigation";

export function WorkspaceHome(): ReactNode {
  const runs = useRuns();
  const all = runs.data?.runs ?? [];
  const running = all.filter((r) => r.active).length;
  const ended = all.filter((r) => !r.active && r.result === "completed").length;
  const stopped = all.filter((r) => !r.active && r.result === null && !r.incompatible).length;
  const counts = [
    running > 0 ? `${running} running` : null,
    stopped > 0 ? `${stopped} stopped` : null,
    ended > 0 ? `${ended} completed` : null,
  ].filter((part) => part !== null);
  return (
    <main className="atlas-backdrop home" aria-label="Workspace home">
      <div className="home-strip">
        <WorkspaceNavigation selectedRunId={null} />
      </div>
      <section className="home-anchor">
        {runs.isError ? (
          <>
            <h1 className="home-title">AIBuildAI Workspace</h1>
            <p className="home-lead">The run list could not be loaded; retrying.</p>
          </>
        ) : runs.data !== undefined && all.length === 0 ? (
          <>
            <h1 className="home-title">No runs yet</h1>
            <p className="home-lead">Start an AIBuildAI run. It will appear here automatically.</p>
          </>
        ) : (
          <>
            <h1 className="home-title">AIBuildAI Workspace</h1>
            <p className="home-lead">Select a run to explore its execution atlas.</p>
            {counts.length > 0 && <p className="home-counts num">{counts.join(" · ")}</p>}
          </>
        )}
      </section>
    </main>
  );
}
