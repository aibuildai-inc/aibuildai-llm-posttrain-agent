import { useParams } from "react-router-dom";

// The selected run is the `/run/:runId` route segment, so every component
// under the run page reads it from the router and every server-state key
// carries it: nothing fetched for one run can be shown under another.
export function useRunId(): string {
  const { runId } = useParams();
  if (runId === undefined) throw new Error("no run id in the route");
  return runId;
}

// The shortest run-id prefix, at least eight characters, that is unique
// among the runs on screen. Identity, keys, and routes always use the full
// 32-character id; this is only what a person reads.
export function shortRunId(runId: string, others: readonly string[]): string {
  let length = 8;
  while (
    length < runId.length &&
    others.some((o) => o !== runId && o.startsWith(runId.slice(0, length)))
  )
    length += 1;
  return runId.slice(0, length);
}
