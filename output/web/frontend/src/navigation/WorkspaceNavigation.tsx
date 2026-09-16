// The one navigation strip of the Workspace: the Workspace control (the
// all-run home), the global run selector, and, on a run page, that run's
// Search-scope crumbs after it. The Workspace control and the run's root
// scope control are two different things and never share one icon.
import { LayoutGrid } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-aria-components";
import { useNavigate } from "react-router-dom";
import { useRuns } from "../app/runs";
import { RunSelector } from "./RunSelector";

export function WorkspaceNavigation(props: {
  selectedRunId: string | null;
  children?: ReactNode;
}): ReactNode {
  const runs = useRuns();
  const navigate = useNavigate();
  return (
    <nav className="scope-breadcrumb" aria-label="Workspace navigation">
      <Link className="scope-crumb workspace-link" href="/">
        <LayoutGrid size={12} strokeWidth={1.75} aria-hidden />
        Workspace
      </Link>
      <RunSelector
        runs={runs.data?.runs ?? []}
        selectedRunId={props.selectedRunId}
        onSelect={(next) => {
          // Another run: a new history entry with none of the previous
          // run's execution, scope, or file, which are local to it. Back
          // returns to the previous run and its query.
          void navigate(`/run/${next}`);
        }}
      />
      {props.children}
    </nav>
  );
}
