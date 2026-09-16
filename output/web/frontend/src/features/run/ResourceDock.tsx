import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronDown, ChevronUp, Pause, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "react-aria-components";
import { fetchResourceHistory, fetchSystemResources, type ResourceSample, type SystemResources } from "../../api/client";
import { progressTier } from "../../format";

const TIER_COLORS: Record<ReturnType<typeof progressTier>, string> = {
  low: "var(--aibuildai-status-success)",
  mid: "var(--aibuildai-status-warning)",
  high: "var(--aibuildai-status-error)",
  over: "var(--aibuildai-status-error)",
};

function tierColor(pct: number): string {
  return TIER_COLORS[progressTier(pct)];
}

function Bar(props: { pct: number; color: string; width?: number }): ReactNode {
  return (
    <span className="res-bar" style={{ width: props.width ?? 100 }}>
      <span className="res-bar-fill" style={{ width: `${Math.min(100, props.pct)}%`, background: props.color }} />
    </span>
  );
}

function GpuRow(props: { gpu: SystemResources["gpus"][0]; owned: boolean }): ReactNode {
  const { gpu, owned } = props;
  const utilPct = gpu.utilization_pct ?? 0;
  const memPct = Math.round((gpu.memory_used_mb / gpu.memory_total_mb) * 100);
  const bwPct = gpu.memory_bw_pct ?? 0;
  return (
    <div className={`res-gpu-row${owned ? " res-gpu-owned" : ""}`}>
      <span className="res-gpu-idx">{gpu.index}</span>
      <span className="res-gpu-name">{gpu.name}</span>
      <span className={`res-gpu-temp${(gpu.temperature_c ?? 0) >= 75 ? " hot" : ""}`}>
        {gpu.temperature_c ?? "—"}°C
      </span>
      <span className="res-gpu-pwr num">
        {gpu.power_w?.toFixed(0) ?? "—"}W
        <span className="res-quiet">/{gpu.power_cap_w?.toFixed(0) ?? "—"}W</span>
      </span>
      <span className="res-meter">
        <span className="res-meter-label">UTIL</span>
        <Bar pct={utilPct} color={tierColor(utilPct)} />
        <span className="res-meter-val num" style={{ color: tierColor(utilPct) }}>{utilPct}%</span>
      </span>
      <span className="res-meter res-meter-wide">
        <span className="res-meter-label">MEM</span>
        <Bar pct={memPct} color={tierColor(memPct)} />
        <span className="res-meter-val num" style={{ color: tierColor(memPct) }}>
          {(gpu.memory_used_mb / 1024).toFixed(1)}<span className="res-quiet">/{(gpu.memory_total_mb / 1024).toFixed(0)}</span>
        </span>
      </span>
      <span className="res-meter res-meter-sm">
        <span className="res-meter-label">BW</span>
        <Bar pct={bwPct} color="var(--aibuildai-accent)" width={50} />
        <span className="res-meter-val num">{bwPct}%</span>
      </span>
    </div>
  );
}

function SysItem(props: { label: string; value: string; pct: number; note?: string; alert?: boolean }): ReactNode {
  const color = tierColor(props.pct);
  return (
    <span className="res-sys-item">
      <span className="res-sys-label">{props.label}</span>
      <span className={`res-sys-val num${props.alert ? " res-alert" : ""}`}>{props.value}</span>
      <Bar pct={props.pct} color={color} width={50} />
      {props.note && <span className={`res-sys-note${props.alert ? " res-alert" : ""}`}>{props.note}</span>}
    </span>
  );
}

const GPU_CSS_COLORS = [
  "--aibuildai-highlight",
  "--aibuildai-status-success",
  "--aibuildai-status-warning",
  "--aibuildai-status-error",
];

const GAP_BREAK_S = 45;

function resolveColor(cssVar: string, el: Element): string {
  const val = getComputedStyle(el).getPropertyValue(cssVar).trim();
  return val || "currentColor";
}

function fmtDuration(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h${m > 0 ? `${m}m` : ""}`;
  return `${m}m`;
}

function TimeSeriesChart(props: {
  samples: ResourceSample[];
  series: { label: string; cssVar: string; extract: (s: ResourceSample) => number | null; dimmed?: boolean }[];
  yLabel: string;
  yMax?: number;
  height?: number;
  windowS: number | null;
}): ReactNode {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { samples, series, yLabel, height = 120, windowS } = props;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || samples.length === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = height;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    const inkSecondary = resolveColor("--aibuildai-atlas-ink-secondary", canvas);
    const border = resolveColor("--aibuildai-atlas-border", canvas);

    const padL = 44, padR = 8, padT = 6, padB = 22;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;

    const fullTMax = samples[samples.length - 1]!.t;
    const tMin = windowS !== null ? Math.max(0, fullTMax - windowS) : samples[0]!.t;
    const tMax = fullTMax;
    const tRange = Math.max(tMax - tMin, 1);

    const visible = windowS !== null ? samples.filter((s) => s.t >= tMin) : samples;

    let yMaxVal = props.yMax ?? 0;
    if (!props.yMax) {
      for (const s of visible) {
        for (const ser of series) {
          const v = ser.extract(s);
          if (v !== null && v > yMaxVal) yMaxVal = v;
        }
      }
      yMaxVal = Math.ceil(yMaxVal * 1.1) || 100;
    }

    ctx.clearRect(0, 0, w, h);

    ctx.strokeStyle = border;
    ctx.lineWidth = 0.5;
    const gridLines = 4;
    for (let i = 0; i <= gridLines; i++) {
      const y = padT + (plotH / gridLines) * i;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();
    }

    ctx.fillStyle = inkSecondary;
    ctx.font = "9px system-ui, sans-serif";
    ctx.textAlign = "right";
    for (let i = 0; i <= gridLines; i++) {
      const y = padT + (plotH / gridLines) * i;
      const val = yMaxVal * (1 - i / gridLines);
      ctx.fillText(val >= 1000 ? `${(val / 1024).toFixed(0)}G` : `${Math.round(val)}`, padL - 4, y + 3);
    }

    ctx.textAlign = "center";
    const tickCount = Math.min(6, Math.max(2, Math.floor(plotW / 80)));
    for (let i = 0; i <= tickCount; i++) {
      const t = tMin + (tRange / tickCount) * i;
      const x = padL + (plotW / tickCount) * i;
      ctx.fillText(fmtDuration(t), x, h - 4);
    }

    for (const ser of series) {
      ctx.strokeStyle = resolveColor(ser.cssVar, canvas);
      ctx.lineWidth = ser.dimmed ? 1 : 1.5;
      ctx.globalAlpha = ser.dimmed ? 0.15 : 1;
      ctx.beginPath();
      let started = false;
      let prevT = -Infinity;
      for (const s of visible) {
        const v = ser.extract(s);
        if (v === null) { started = false; continue; }
        if (s.t - prevT > GAP_BREAK_S && started) { started = false; ctx.stroke(); ctx.beginPath(); }
        const x = padL + ((s.t - tMin) / tRange) * plotW;
        const y = padT + plotH - (v / yMaxVal) * plotH;
        if (!started) { ctx.moveTo(x, y); started = true; }
        else ctx.lineTo(x, y);
        prevT = s.t;
      }
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    ctx.fillStyle = inkSecondary;
    ctx.font = "8px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(yLabel, padL + 4, padT + 10);
  }, [samples, series, height, props.yMax, yLabel, windowS]);

  useEffect(() => { draw(); }, [draw]);
  useEffect(() => {
    const obs = new ResizeObserver(() => draw());
    if (canvasRef.current) obs.observe(canvasRef.current);
    return () => obs.disconnect();
  }, [draw]);

  return <canvas ref={canvasRef} className="res-chart-canvas" style={{ width: "100%", height }} />;
}

function HistoryPanel(props: { runId: string; framePositionS: number | null }): ReactNode {
  const [playing, setPlaying] = useState(true);
  const [windowS, setWindowS] = useState<number | null>(null);
  const [selectedGpu, setSelectedGpu] = useState<number | null>(null);
  const historical = props.framePositionS !== null;

  const query = useQuery({
    queryKey: ["resource-history", props.runId],
    queryFn: () => fetchResourceHistory(props.runId),
    refetchInterval: !historical && playing ? 15000 : false,
    retry: false,
  });
  const allSamples = query.data;
  if (!allSamples || allSamples.length === 0) {
    return <div className="res-history-empty">No resource history recorded yet.</div>;
  }
  const samples = historical
    ? allSamples.filter((s) => s.t <= props.framePositionS!)
    : allSamples;
  if (samples.length === 0) {
    return <div className="res-history-empty">No resource data at this frame.</div>;
  }

  const gpuIds = [...new Set(samples.flatMap((s) => s.gpus.map((g) => g.index)))].sort((a, b) => a - b);
  const gpuUtilSeries = gpuIds.map((gpuId, i) => ({
    label: `GPU ${gpuId}`,
    cssVar: GPU_CSS_COLORS[i % GPU_CSS_COLORS.length]!,
    extract: (s: ResourceSample) => s.gpus.find((g) => g.index === gpuId)?.utilization_pct ?? null,
    dimmed: selectedGpu !== null && selectedGpu !== gpuId,
  }));
  const gpuMemSeries = gpuIds.map((gpuId, i) => ({
    label: `GPU ${gpuId}`,
    cssVar: GPU_CSS_COLORS[i % GPU_CSS_COLORS.length]!,
    extract: (s: ResourceSample) => {
      const g = s.gpus.find((gpu) => gpu.index === gpuId);
      return g ? g.memory_used_mb / 1024 : null;
    },
    dimmed: selectedGpu !== null && selectedGpu !== gpuId,
  }));
  const maxGpuMem = Math.max(...samples.flatMap((s) => s.gpus.map((g) => g.memory_total_mb / 1024)), 1);

  return (
    <div className="res-history">
      <div className="res-history-controls">
        <div className="res-history-legend">
          {gpuUtilSeries.map((s, i) => {
            const gpuId = gpuIds[i]!;
            const active = selectedGpu === gpuId;
            const dimmed = selectedGpu !== null && !active;
            return (
              <span
                key={s.label}
                className="res-legend-item"
                data-active={active || undefined}
                data-dimmed={dimmed || undefined}
                onClick={() => setSelectedGpu(active ? null : gpuId)}
              >
                <span className="res-legend-dot" style={{ background: `var(${s.cssVar})` }} />
                <span className="res-legend-label">{s.label}</span>
              </span>
            );
          })}
        </div>
        <div className="res-history-buttons">
          <button className="res-ctrl-btn" onClick={() => setPlaying(!playing)} title={playing ? "Pause" : "Play"}>
            {playing ? <Pause size={10} /> : <Play size={10} />}
          </button>
          <button className="res-ctrl-btn" onClick={() => { setPlaying(true); setWindowS(null); }} title="Reset">
            <RotateCcw size={10} />
          </button>
          <span className="res-zoom-group">
            {([["5m", 300], ["15m", 900], ["1h", 3600], ["All", null]] as const).map(([label, val]) => (
              <button key={label} className={`res-zoom-btn${windowS === val ? " res-zoom-active" : ""}`} onClick={() => setWindowS(val)}>{label}</button>
            ))}
          </span>
        </div>
      </div>
      <div className="res-chart-group">
        <TimeSeriesChart samples={samples} series={gpuUtilSeries} yLabel="GPU Util %" yMax={100} windowS={windowS} />
      </div>
      <div className="res-chart-group">
        <TimeSeriesChart samples={samples} series={gpuMemSeries} yLabel="GPU Mem (GB)" yMax={maxGpuMem} windowS={windowS} />
      </div>
      <div className="res-chart-group">
        <TimeSeriesChart
          samples={samples}
          series={[{ label: "CPU Load", cssVar: "--aibuildai-highlight", extract: (s) => s.cpu_load_1m }]}
          yLabel="CPU Load (1m)"
          windowS={windowS}
        />
      </div>
      <div className="res-chart-group">
        <TimeSeriesChart
          samples={samples}
          series={[{ label: "RAM", cssVar: "--aibuildai-status-warning", extract: (s) => s.memory_used_mb / 1024 }]}
          yLabel="RAM (GB)"
          yMax={samples[0]!.memory_total_mb / 1024}
          windowS={windowS}
        />
      </div>
    </div>
  );
}

export function ResourceDock(props: { runId: string; framePositionS: number | null; runLive: boolean }): ReactNode {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"live" | "timeline">("live");
  const showLiveTab = props.runLive && props.framePositionS === null;
  const effectiveTab = showLiveTab ? tab : "timeline";
  const liveQuery = useQuery({
    queryKey: ["system-resources"],
    queryFn: fetchSystemResources,
    enabled: showLiveTab,
    refetchInterval: showLiveTab && open && effectiveTab === "live" ? 5000 : false,
    retry: false,
  });

  const data = liveQuery.data;
  const avgUtil = showLiveTab && data ? Math.round(data.gpus.reduce((s, g) => s + (g.utilization_pct ?? 0), 0) / Math.max(1, data.gpus.length)) : null;

  return (
    <div className="res-dock" data-open={open || undefined}>
      <Button className="res-dock-toggle" onPress={() => setOpen(!open)}>
        <Activity size={12} strokeWidth={2} aria-hidden />
        <span>Resources</span>
        {avgUtil !== null && <span className="res-dock-avg num">GPU {avgUtil}%</span>}
        {open ? <ChevronDown size={12} strokeWidth={2} aria-hidden /> : <ChevronUp size={12} strokeWidth={2} aria-hidden />}
      </Button>
      {open && (
        <div className="res-dock-body">
          {showLiveTab && (
            <div className="res-dock-tabs">
              <button className={`res-tab${effectiveTab === "live" ? " res-tab-active" : ""}`} onClick={() => setTab("live")}>Live</button>
              <button className={`res-tab${effectiveTab === "timeline" ? " res-tab-active" : ""}`} onClick={() => setTab("timeline")}>Timeline</button>
            </div>
          )}
          {effectiveTab === "live" && data !== undefined && (
            <>
              <div className="res-dock-head">
                <span className="res-dock-driver num">NVIDIA · CUDA</span>
                {data.system.load_avg != null && data.system.load_avg.length > 0 && (
                  <span className="res-dock-stat">Load <b className="num">{data.system.load_avg[0]!.toFixed(1)}</b></span>
                )}
                <span className="res-dock-stat">CPU <b className="num">{data.system.cpu_count}</b></span>
                <span className="res-dock-hostname num">{data.system.hostname}</span>
              </div>
              <div className="res-gpu-stack">
                {data.gpus.map((gpu) => (
                  <GpuRow key={gpu.index} gpu={gpu} owned={false} />
                ))}
              </div>
              <div className="res-sys-strip">
                <SysItem
                  label="Mem"
                  value={`${((data.system.memory_total_mb - data.system.memory_available_mb) / 1024).toFixed(0)}/${(data.system.memory_total_mb / 1024).toFixed(0)}G`}
                  pct={Math.round(((data.system.memory_total_mb - data.system.memory_available_mb) / data.system.memory_total_mb) * 100)}
                />
                {data.system.swap_total_mb > 0 && (
                  <SysItem
                    label="Swap"
                    value={`${((data.system.swap_total_mb - data.system.swap_free_mb) / 1024).toFixed(1)}/${(data.system.swap_total_mb / 1024).toFixed(0)}G`}
                    pct={Math.round(((data.system.swap_total_mb - data.system.swap_free_mb) / data.system.swap_total_mb) * 100)}
                    alert={((data.system.swap_total_mb - data.system.swap_free_mb) / data.system.swap_total_mb) > 0.8}
                  />
                )}
                {data.system.disks.map((d) => {
                  const pct = d.total_gb > 0 ? Math.round((d.used_gb / d.total_gb) * 100) : 0;
                  return (
                    <SysItem
                      key={d.mount}
                      label={d.mount}
                      value={`${d.used_gb > 1000 ? `${Math.round(d.used_gb / 1024)}T` : `${d.used_gb}G`}/${d.total_gb > 1000 ? `${Math.round(d.total_gb / 1024)}T` : `${d.total_gb}G`}`}
                      pct={pct}
                      note={`${d.free_gb > 1000 ? `${(d.free_gb / 1024).toFixed(1)}T` : `${d.free_gb}G`} free`}
                      alert={pct >= 90}
                    />
                  );
                })}
              </div>
            </>
          )}
          {effectiveTab === "timeline" && <HistoryPanel runId={props.runId} framePositionS={props.framePositionS} />}
        </div>
      )}
    </div>
  );
}
