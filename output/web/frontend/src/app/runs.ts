import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetchRuns, type RunsResponse, type RunSummary } from "../api/client";

// Every indexed run, polled while the page is open so the selector follows
// runs starting, ending, and being restored. One query identity for the
// whole Workspace: the home page and every run page share it.
export function useRuns(): UseQueryResult<RunsResponse> {
  return useQuery({
    queryKey: ["runs"],
    queryFn: fetchRuns,
    refetchInterval: 2000,
    retry: false,
  });
}

export function findRun(
  runs: RunsResponse | undefined,
  runId: string,
): RunSummary | undefined {
  return runs?.runs.find((run) => run.run_id === runId);
}
