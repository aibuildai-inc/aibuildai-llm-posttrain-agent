import type { MetricDirection } from "./api/client";

export function bestMetric(
  metrics: number[],
  direction: MetricDirection,
): number | null {
  if (metrics.length === 0) return null;
  return direction === "max" ? Math.max(...metrics) : Math.min(...metrics);
}
