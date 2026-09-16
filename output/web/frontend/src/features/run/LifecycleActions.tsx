import { Pause, Play } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "react-aria-components";
import type { RunCapabilitiesResponse } from "../../api/client";

export function LifecycleActions(props: {
  capabilities: RunCapabilitiesResponse | undefined;
  onPause: () => void;
  onResume: () => void;
  failure: RunCapabilitiesResponse["failure"];
  onDismissFailure: () => void;
  onRetry: () => void;
}): ReactNode {
  const control = props.capabilities?.control;
  const label = control?.pending
    ? control.kind === "pause" ? "Pausing…" : "Resuming…"
    : control?.kind === "pause" ? "Pause run" : "Resume";
  return (
    <span className="run-actions">
      {control !== null && control !== undefined ? (
        <span title={control.reason ?? undefined}>
          <Button
            className={control.kind === "resume" ? "action-button action-button-primary" : "action-button"}
            isDisabled={!control.enabled}
            onPress={control.kind === "pause" ? props.onPause : props.onResume}
          >
            {control.kind === "pause" ? <Pause size={13} aria-hidden /> : <Play size={13} aria-hidden />}
            {label}
          </Button>
        </span>
      ) : null}
      {props.failure !== null && (
        <span className="action-error" role="alert">
          <span>
            {props.failure.kind === "pause" ? "Pause failed" : "Resume failed"}: {props.failure.error}
          </span>
          <Button onPress={props.onDismissFailure}>Dismiss</Button>
          <Button onPress={props.onRetry}>Try again</Button>
        </span>
      )}
    </span>
  );
}
