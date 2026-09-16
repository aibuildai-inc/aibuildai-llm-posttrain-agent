import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

// The one typed HTTP client. Every call goes through the generated OpenAPI
// types, so a schema change on the Python side fails the typecheck here
// instead of failing in the browser. Every per-run call is namespaced by
// the run id: the Workspace backend serves every indexed run, and forwards
// each call to the member that answers that run right now.
export const client = createClient<paths>();

export type RunsResponse = components["schemas"]["RunsResponse"];
export type RunCapabilitiesResponse =
  components["schemas"]["RunCapabilitiesResponse"];
export type RunSummary = components["schemas"]["RunSummary"];
export type RunResponse = components["schemas"]["RunResponse"];
export type MetricDirection = RunResponse["metric_direction"];
export type ExecutionSummary = components["schemas"]["ExecutionSummary"];
export type ScoredExecution = components["schemas"]["ScoredExecution"];
export type ExecutionDetailResponse =
  components["schemas"]["ExecutionDetailResponse"];
export type TranscriptResponse = components["schemas"]["TranscriptResponse"];
export type TranscriptTurn = components["schemas"]["TranscriptTurn"];
export type FoldBlock = components["schemas"]["FoldBlock"];
export type TextBlock = components["schemas"]["TextBlock"];
export type ToolCallBlock = components["schemas"]["ToolCallBlock"];
export type FactRow = components["schemas"]["FactRow"];
export type MetricComponent = components["schemas"]["MetricComponent"];
export type MetricSeries = components["schemas"]["MetricSeries"];
export type MetricSeriesResponse = components["schemas"]["MetricSeriesResponse"];
export type WorkspaceEntry = components["schemas"]["WorkspaceEntry"];
export type WorkspaceEntriesResponse =
  components["schemas"]["WorkspaceEntriesResponse"];
export type WorkspaceStatResponse =
  components["schemas"]["WorkspaceStatResponse"];
export type WorkspaceTextResponse =
  components["schemas"]["WorkspaceTextResponse"];

// A request the service answered with 404 names a run the index does not
// know (or an execution it does not know); 503 names a known run no
// provider can answer right now. The page tells them apart by status.
export class RequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function require<T>(data: T | undefined, response: Response, what: string): T {
  if (data === undefined)
    throw new RequestError(`${what} request failed: ${response.status}`, response.status);
  return data;
}

export type SystemResources = {
  gpus: { index: number; name: string; temperature_c: number | null; power_w: number | null; power_cap_w: number | null; memory_used_mb: number; memory_total_mb: number; utilization_pct: number | null; memory_bw_pct: number | null }[];
  processes: { gpu: number; pid: number; user: string; gpu_memory_mb: number; command: string }[];
  system: { hostname: string; cpu_count: number; load_avg: number[] | null; memory_total_mb: number; memory_available_mb: number; swap_total_mb: number; swap_free_mb: number; disks: { mount: string; total_gb: number; used_gb: number; free_gb: number }[] };
};

export async function fetchSystemResources(): Promise<SystemResources> {
  const resp = await fetch("/api/v1/system/resources");
  if (!resp.ok) throw new RequestError("system resources failed", resp.status);
  return resp.json() as Promise<SystemResources>;
}

export type ResourceSample = {
  t: number;
  cpu_load_1m: number | null;
  cpu_count: number;
  memory_used_mb: number;
  memory_total_mb: number;
  gpus: { index: number; utilization_pct: number | null; memory_bw_pct: number | null; memory_used_mb: number; memory_total_mb: number }[];
};

export async function fetchResourceHistory(runId: string): Promise<ResourceSample[]> {
  const resp = await fetch(`/api/v1/runs/${runId}/resources/history`);
  if (!resp.ok) throw new RequestError("resource history failed", resp.status);
  return resp.json() as Promise<ResourceSample[]>;
}

export async function fetchRuns(): Promise<RunsResponse> {
  const { data, response } = await client.GET("/api/v1/runs");
  return require(data, response, "runs");
}

export async function fetchRun(runId: string, atS?: number): Promise<RunResponse> {
  const { data, response } = await client.GET("/api/v1/runs/{run_id}/run", {
    params: { path: { run_id: runId }, query: { at_s: atS } },
  });
  return require(data, response, "run");
}

export async function fetchExecution(
  runId: string,
  path: string,
  revision?: number,
): Promise<ExecutionDetailResponse> {
  const { data, response } = await client.GET(
    "/api/v1/runs/{run_id}/execution",
    { params: { path: { run_id: runId }, query: { path, revision } } },
  );
  return require(data, response, "execution");
}

export async function fetchMetrics(
  runId: string,
  path: string,
): Promise<MetricSeriesResponse> {
  const { data, response } = await client.GET(
    "/api/v1/runs/{run_id}/metrics",
    { params: { path: { run_id: runId }, query: { path } } },
  );
  return require(data, response, "metrics");
}

export async function fetchTranscript(
  runId: string,
  path: string,
  revision?: number,
): Promise<TranscriptResponse> {
  const { data, response } = await client.GET(
    "/api/v1/runs/{run_id}/transcript",
    { params: { path: { run_id: runId }, query: { path, revision } } },
  );
  return require(data, response, "transcript");
}

export async function fetchWorkspaceEntries(
  runId: string,
  path: string,
): Promise<WorkspaceEntriesResponse> {
  const { data, response } = await client.GET(
    "/api/v1/runs/{run_id}/workspace/entries",
    { params: { path: { run_id: runId }, query: { path } } },
  );
  return require(data, response, "workspace entries");
}

export async function fetchWorkspaceStat(
  runId: string,
  path: string,
): Promise<WorkspaceStatResponse> {
  const { data, response } = await client.GET(
    "/api/v1/runs/{run_id}/workspace/stat",
    { params: { path: { run_id: runId }, query: { path } } },
  );
  return require(data, response, "workspace stat");
}

export async function fetchWorkspaceText(
  runId: string,
  path: string,
): Promise<WorkspaceTextResponse> {
  const { data, response } = await client.GET(
    "/api/v1/runs/{run_id}/workspace/text",
    { params: { path: { run_id: runId }, query: { path } } },
  );
  return require(data, response, "workspace text");
}

// The two byte routes are browser-driven (an iframe, an img, a download
// link), so they are addressed by URL, not fetched into memory here.
export function workspaceFileUrl(
  runId: string,
  route: "raw" | "download",
  path: string,
): string {
  return `/api/v1/runs/${runId}/workspace/${route}?path=${encodeURIComponent(path)}`;
}

// ---- Run lifecycle actions ----

// The CSRF token is read once per page life and sent back on every
// mutation in a custom header. A cross-site page can neither read the
// token nor set the header, so it cannot mutate.
export async function fetchCsrf(): Promise<string> {
  const { data, response } = await client.GET("/api/v1/csrf");
  return require(data, response, "csrf").token;
}

function csrfHeaders(token: string): Record<string, string> {
  return { "X-AIBuildAI-Csrf": token };
}

export async function fetchCapabilities(
  runId: string,
): Promise<RunCapabilitiesResponse> {
  const { data, response } = await client.GET(
    "/api/v1/runs/{run_id}/capabilities",
    { params: { path: { run_id: runId } } },
  );
  return require(data, response, "capabilities");
}

export async function postRunAction(
  runId: string,
  kind: "pause" | "resume",
  csrf: string,
): Promise<void> {
  const { response } = await client.POST(
    kind === "pause"
      ? "/api/v1/runs/{run_id}/actions/pause"
      : "/api/v1/runs/{run_id}/actions/resume",
    { params: { path: { run_id: runId } }, headers: csrfHeaders(csrf) },
  );
  if (!response.ok)
    throw new RequestError(`${kind} action failed: ${response.status}`, response.status);
}
