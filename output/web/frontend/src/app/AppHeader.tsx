import { AlertTriangle, Clock, PanelRightOpen } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "react-aria-components";
import type { RunResponse } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { fmtClock, fmtCost, fmtHhmmss, fmtMetric, progressTier } from "../format";

function ProgressBar(props: { current: string; total: string | null; pct: number | null; colored?: boolean }): ReactNode {
  const clampedWidth = props.pct !== null ? Math.min(100, props.pct) : null;
  const tier = props.pct !== null && props.colored ? progressTier(props.pct) : null;
  return (
    <span className="progress-bar">
      <span className="num">{props.current}</span>
      {clampedWidth !== null && (
        <span className="progress-bar-track">
          <span className="progress-bar-fill" data-tier={tier} style={{ width: `${clampedWidth}%` }} />
        </span>
      )}
      {props.total !== null && <span className="num">{props.total}</span>}
    </span>
  );
}

function WarningChip(props: { warnings: RunResponse["warnings"]; count: number }): ReactNode {
  const [open, setOpen] = useState(false);
  const n = props.warnings.length > 0 ? props.warnings.length : props.count;
  if (n === 0) return null;
  return (
    <span
      className="warn-chip"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <AlertTriangle size={12} strokeWidth={2} aria-hidden />
      <span>{n}</span>
      {open && (
        <span className="warn-tooltip">
          <span className="warn-tooltip-head">
            {n} Warning{n > 1 ? "s" : ""}
          </span>
          {props.warnings.map((w, i) => (
            <span key={i} className="warn-tooltip-item">
              <span className="warn-tooltip-row1">
                <span className="warn-tooltip-source">{String(w.source ?? "")}</span>
                <span className="warn-tooltip-cat">{String(w.category ?? "")}</span>
                <span className="warn-tooltip-time num">+{fmtHhmmss(Number(w.at_s ?? 0))}</span>
              </span>
              <span className="warn-tooltip-msg">{String(w.message ?? "")}</span>
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

function Fact(props: { label: string; value: string; strong?: boolean }): ReactNode {
  return (
    <span className="run-fact">
      <span className="run-fact-label">{props.label}</span>
      <span className={`num${props.strong ? " run-fact-strong" : ""}`}>{props.value}</span>
    </span>
  );
}

export function AppHeader(props: { children?: ReactNode }): ReactNode {
  return (
    <header className="run-bar">
      <span className="brand">AIBuildAI</span>
      {props.children ?? <span className="quiet header-workspace">Workspace</span>}
    </header>
  );
}

function LiveClock(): ReactNode {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return <>{now.toLocaleTimeString()}</>;
}

export type Liveness = "live" | "stale" | "disconnected" | "restoring" | "inactive" | "unavailable";

export function RunFacts(props: {
  run: RunResponse | undefined;
  badgeRun?: RunResponse;
  liveness: Liveness;
  diagnostic: string | null;
  timelineClock?: string | null;
  onOpenWorkspace: (() => void) | null;
  actions?: ReactNode;
  runSelector?: ReactNode;
}): ReactNode {
  const { run } = props;
  const lastGenerated = run !== undefined ? ` — last update ${fmtClock(run.generated_at_unix)}` : "";
  const badgeRun = props.badgeRun ?? run;
  const statusWord = badgeRun?.status_word ?? run?.status_word ?? "";
  const statusToken = badgeRun?.status_style_token ?? run?.status_style_token ?? "pending";
  const activeCount = run?.active_execution_count ?? 0;
  const badgeWord = activeCount > 0
    ? `${statusWord} · ${activeCount} active`
    : statusWord;

  return (
    <>
      {run === undefined ? (
        <span className="quiet">
          {props.liveness === "restoring"
            ? "restoring the run from its journal…"
            : props.liveness === "unavailable"
              ? (props.diagnostic ?? "this run cannot be shown")
              : "waiting for the first run view…"}
        </span>
      ) : (
        <>
          {props.runSelector}
          <StatusBadge word={badgeWord} token={statusToken} />
          {props.timelineClock != null && (
            <span className="quiet num replay-header-clock">Timeline {props.timelineClock}</span>
          )}
          <span className="run-divider" aria-hidden />
          <ProgressBar
            current={fmtHhmmss(run.elapsed_s)}
            total={run.remaining_s !== null ? fmtHhmmss(run.elapsed_s + run.remaining_s) : null}
            pct={run.remaining_s !== null && (run.elapsed_s + run.remaining_s) > 0
              ? (run.elapsed_s / (run.elapsed_s + run.remaining_s)) * 100 : null}
            colored
          />
          <ProgressBar
            current={fmtCost(run.total_cost)}
            total={run.cost_budget_usd !== null ? `$${run.cost_budget_usd.toFixed(2)}` : null}
            pct={run.cost_budget_usd !== null && run.cost_budget_usd > 0
              ? (run.total_cost / run.cost_budget_usd) * 100 : null}
            colored
          />
          <Fact label="model" value={run.model} />
          {run.selected_metric !== null && (
            <Fact
              label={`selected ${run.metric_name}`}
              value={fmtMetric(run.selected_metric)}
              strong
            />
          )}
          <WarningChip warnings={run.warnings ?? []} count={run.warnings_count} />
        </>
      )}
      <span className="run-liveness">
        {props.actions}
        {props.liveness === "disconnected" && (
          <span className="liveness-chip" data-kind="disconnected">
            DISCONNECTED{lastGenerated}
          </span>
        )}
        {props.liveness === "stale" && (
          <span className="liveness-chip" data-kind="stale">
            STALE{lastGenerated}
          </span>
        )}
        {props.liveness === "restoring" && run !== undefined && (
          <span className="liveness-chip" data-kind="stale">
            RESTORING{lastGenerated}
          </span>
        )}
        {props.liveness === "unavailable" && run !== undefined && (
          <span className="liveness-chip" data-kind="disconnected">
            UNAVAILABLE — {props.diagnostic}
          </span>
        )}
        {props.liveness === "inactive" && badgeRun !== undefined && (
          <span className="quiet">{badgeRun.result_recorded ? "final history" : "Run inactive"}</span>
        )}
      </span>
      {run !== undefined && (
        <span className="header-clock num">
          <Clock size={11} strokeWidth={1.75} aria-hidden />
          {props.timelineClock != null
            ? props.timelineClock
            : props.liveness === "live" && !run.result_recorded
              ? <LiveClock />
              : fmtClock(run.generated_at_unix)}
        </span>
      )}
      {props.onOpenWorkspace !== null && (
        <Button className="inspector-close" aria-label="Open workspace" onPress={props.onOpenWorkspace}>
          <PanelRightOpen size={15} strokeWidth={1.75} aria-hidden />
        </Button>
      )}
    </>
  );
}
