// The global run selector: one React Aria Select over every indexed run,
// grouped by lifecycle. The trigger shows the selected run's status dot,
// task name, and unique short id, or the Workspace's own prompt when no
// run is selected; each row adds its clock, model, best metric, and how it
// is served. React Aria owns the popover, focus, keyboard navigation,
// typeahead, sections, and selection semantics; nothing here is hand-rolled.
import { Check, ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import {
  Button,
  Header,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  Popover,
  Select,
  SelectValue,
} from "react-aria-components";
import type { RunSummary } from "../api/client";
import { shortRunId } from "../app/runId";
import { StatusDot } from "../components/StatusBadge";
import { fmtMetric } from "../format";

export interface RunSelectorProps {
  runs: RunSummary[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}

function fmtElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

// How long ago an instant was, in the coarsest unit that still reads.
function fmtAgo(unixSeconds: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - unixSeconds));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// The row's clock: a live run's elapsed time, an ended run's end, a
// stopped run's last update.
function fmtWhen(run: RunSummary): string {
  if (run.active)
    return run.elapsed_s !== null ? fmtElapsed(run.elapsed_s) : "";
  const at = run.ended_at_unix ?? run.updated_at_unix ?? run.started_at_unix;
  return at !== null ? fmtAgo(at) : "";
}

// Projection availability, shown only when it adds information to the status.
function fmtAvailability(run: RunSummary): string | null {
  if (run.availability === "restoring") return "restoring";
  if (run.availability === "unavailable") return "unavailable";
  return null;
}

const SECTIONS: { id: string; title: string; member: (run: RunSummary) => boolean }[] = [
  { id: "running", title: "Running", member: (r) => r.active && r.availability !== "unavailable" },
  { id: "resumable", title: "Resumable", member: (r) => !r.active && r.result === null && !r.incompatible && r.availability !== "unavailable" },
  { id: "recent", title: "Recent", member: (r) => !r.active && r.result !== null && r.availability !== "unavailable" },
  { id: "unavailable", title: "Unavailable", member: (r) => r.availability === "unavailable" || r.incompatible },
];

export function RunSelector(props: RunSelectorProps): ReactNode {
  const ids = props.runs.map((r) => r.run_id);
  const current = props.runs.find((r) => r.run_id === props.selectedRunId);
  const sections = SECTIONS.map((section) => ({
    ...section,
    runs: props.runs.filter(section.member),
  })).filter((section) => section.runs.length > 0);
  const empty = props.runs.length === 0;
  return (
    <Select
      className="run-select"
      aria-label="Run"
      placeholder={empty ? "No runs yet" : "Select a run"}
      selectedKey={props.selectedRunId}
      onSelectionChange={(key) => {
        if (typeof key === "string" && key !== props.selectedRunId) props.onSelect(key);
      }}
    >
      <Button className="run-select-trigger">
        <SelectValue<RunSummary>>
          {({ defaultChildren }) =>
            current === undefined ? (
              props.selectedRunId !== null ? (
                <span className="run-select-gone">
                  unknown run{" "}
                  <span className="num">{shortRunId(props.selectedRunId, ids)}</span>
                </span>
              ) : (
                <span className="run-select-placeholder">{defaultChildren}</span>
              )
            ) : (
              <>
                <StatusDot token={current.status_style_token} />
                <span className="run-select-task">{current.task_name}</span>
                <span className="run-select-sep" aria-hidden>
                  ·
                </span>
                <span className="num run-select-id">
                  {shortRunId(current.run_id, ids)}
                </span>
              </>
            )
          }
        </SelectValue>
        <ChevronDown size={12} strokeWidth={1.75} aria-hidden />
      </Button>
      <Popover className="run-select-popover" placement="bottom start">
        <ListBox
          className="run-select-list"
          renderEmptyState={() => (
            <p className="run-select-empty">
              No runs are available. New runs will appear here automatically.
            </p>
          )}
        >
          {sections.map((section) => (
            <ListBoxSection key={section.id} id={section.id}>
              <Header className="run-select-section">{section.title}</Header>
              {section.runs.map((run) => {
                const availability = fmtAvailability(run);
                return (
                  <ListBoxItem
                    key={run.run_id}
                    id={run.run_id}
                    textValue={`${run.task_name} ${shortRunId(run.run_id, ids)}`}
                    className="run-select-row"
                  >
                    <span className="run-select-row-line">
                      <StatusDot token={run.status_style_token} />
                      <span className="run-select-task">{run.task_name}</span>
                      <span className="run-select-spacer" />
                      <span className="num run-select-elapsed">{fmtWhen(run)}</span>
                    </span>
                    <span className="run-select-row-line run-select-row-sub">
                      <span className="num">{shortRunId(run.run_id, ids)}</span>
                      {run.model !== null && (
                        <>
                          <span className="run-select-sep" aria-hidden>
                            ·
                          </span>
                          <span className="run-select-model">{run.model}</span>
                        </>
                      )}
                      <span className="run-select-spacer" />
                      {run.selected_metric !== null && (
                        <span className="num">selected {fmtMetric(run.selected_metric)}</span>
                      )}
                      {availability !== null && (
                        <span className="run-select-availability" data-availability={run.availability}>
                          {availability}
                        </span>
                      )}
                      {run.run_id === props.selectedRunId && (
                        <Check size={12} strokeWidth={2} aria-hidden className="run-select-check" />
                      )}
                    </span>
                  </ListBoxItem>
                );
              })}
            </ListBoxSection>
          ))}
        </ListBox>
      </Popover>
    </Select>
  );
}
