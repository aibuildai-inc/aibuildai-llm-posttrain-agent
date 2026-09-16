// The lifecycle status badge. The color token and word arrive from the API
// (output.status_presentation stays the one owner of status semantics); the
// browser only maps the token onto its CSS variable and renders a tinted
// pill with the status dot. A running badge carries a restrained pulse;
// terminal states are still.
import { motion } from "motion/react";
import type { ReactNode } from "react";
import { useMotionTransition } from "../motionPresets";

const STATUS_VARS: Record<string, string> = {
  success: "var(--aibuildai-atlas-status-success)",
  warning: "var(--aibuildai-atlas-status-warning)",
  error: "var(--aibuildai-atlas-status-error)",
  running: "var(--aibuildai-atlas-status-running)",
  pending: "var(--aibuildai-atlas-status-pending)",
};

export function statusColor(token: string): string {
  return STATUS_VARS[token] ?? "var(--aibuildai-atlas-ink)";
}

export function StatusBadge(props: { word: string; token: string }): ReactNode {
  const transition = useMotionTransition({ duration: 0.16 });
  return (
    <motion.span
      key={props.word}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={transition}
      className="status-badge"
      data-token={props.token}
      style={{ color: statusColor(props.token) }}
    >
      <span
        className={`status-dot${props.token === "running" ? " status-dot-running" : ""}`}
        aria-hidden
      />
      {props.word}
    </motion.span>
  );
}

export function StatusDot(props: { token: string }): ReactNode {
  return (
    <span
      className={`status-dot status-dot-lone${
        props.token === "running" ? " status-dot-running" : ""
      }`}
      style={{ color: statusColor(props.token) }}
      aria-hidden
    />
  );
}
