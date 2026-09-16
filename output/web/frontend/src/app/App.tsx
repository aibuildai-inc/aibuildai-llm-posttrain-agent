import type { ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { RunPage } from "../features/run/RunPage";
import { useRunId } from "./runId";
import { WorkspaceHome } from "../home/WorkspaceHome";
import { WorkspaceNavigation } from "../navigation/WorkspaceNavigation";
import { AppHeader } from "./AppHeader";

// An unknown route is an intentional state inside the shell: the header
// and the run selector stay, so the user can move on from here.
function WorkspaceNotFound(): ReactNode {
  return (
    <div className="app-shell">
      <AppHeader />
      <main className="atlas-backdrop home" aria-label="Not found">
        <div className="home-strip">
          <WorkspaceNavigation selectedRunId={null} />
        </div>
        <section className="home-anchor">
          <h1 className="home-title">Not found</h1>
          <p className="home-lead">This address is not a Workspace page. Select a run above, or return to the Workspace.</p>
        </section>
      </main>
    </div>
  );
}

// Remount the run page whenever the run id changes: every piece of
// run-local state (pending action, failure dialog, timeline, panel
// visibility) belongs to one run, and a remount is the one reset that
// cannot miss a field.
function KeyedRunPage(): ReactNode {
  const runId = useRunId();
  return <RunPage key={runId} />;
}

// `/` is always the Workspace home and never redirects; `/run/<run_id>`
// is the run workbench and stays valid after the run process is gone.
export function App(): ReactNode {
  return (
    <Routes>
      <Route
        path="/"
        element={
          <div className="app-shell">
            <AppHeader />
            <WorkspaceHome />
          </div>
        }
      />
      <Route path="/run/:runId" element={<KeyedRunPage />} />
      <Route path="*" element={<WorkspaceNotFound />} />
    </Routes>
  );
}
