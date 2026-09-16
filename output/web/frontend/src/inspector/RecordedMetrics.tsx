import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, type ReactNode } from "react";
import { fetchMetrics } from "../api/client";
import { useFrame } from "../app/replay";
import { useRunId } from "../app/runId";
import { Section } from "./sections";

const MetricCurves = lazy(() =>
  import("../charts/MetricCurves").then((module) => ({
    default: module.MetricCurves,
  })),
);

export function RecordedMetrics(props: {
  path: string;
  live: boolean;
}): ReactNode {
  const runId = useRunId();
  // Metric points carry no per-frame identity, so replay shows the
  // final recorded series and says so. It is not presented as historical.
  const frame = useFrame();
  const query = useQuery({
    queryKey: ["metrics", runId, props.path],
    queryFn: () => fetchMetrics(runId, props.path),
    refetchInterval: props.live ? 2000 : false,
    retry: false,
  });
  if (
    query.isSuccess &&
    query.data.series.length === 0
  ) {
    return null;
  }
  let body: ReactNode;
  if (query.isPending) {
    body = <p className="quiet chart-empty">Loading recorded metrics…</p>;
  } else if (query.isError) {
    body = (
      <p className="chart-empty metric-series-error">
        Recorded metrics unavailable
      </p>
    );
  } else {
    body = (
      <Suspense fallback={<p className="quiet chart-empty">Loading chart…</p>}>
        <MetricCurves series={query.data.series} />
      </Suspense>
    );
  }
  return (
    <Section title={frame?.historical ? "recorded metrics — final series" : "recorded metrics"}>
      {body}
    </Section>
  );
}
