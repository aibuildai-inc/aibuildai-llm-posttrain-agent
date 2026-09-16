// Small display formatters, matching the Web Workspace conventions.

export function fmtHhmmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(rest)}`;
}

export function fmtCost(cost: number): string {
  return `$${cost.toFixed(2)}`;
}

export function fmtMetric(metric: number): string {
  return Number(metric.toPrecision(6)).toString();
}

export function fmtClock(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString();
}

export function progressTier(pct: number): "low" | "mid" | "high" | "over" {
  if (pct > 100) return "over";
  if (pct >= 80) return "high";
  if (pct >= 50) return "mid";
  return "low";
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
