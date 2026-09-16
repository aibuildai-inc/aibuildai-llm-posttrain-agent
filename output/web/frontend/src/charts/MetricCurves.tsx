// Metric curves as small multiples: one compact chart per recorded
// metric (mixed units on one axis would make every line unreadable), fed
// by the typed MetricSeries that the backend reads from metrics.jsonl.
// The frontend converts series to ECharts
// options here; Python never sends chart configuration. Colors are the
// served project tokens, resolved at render time because a canvas cannot
// read a CSS variable.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { EChartsCoreOption } from "echarts/core";
import { Button } from "react-aria-components";
import type { MetricSeries } from "../api/client";
import { fmtMetric } from "../format";
import { EChartsSurface } from "./EChartsSurface";

type ChartValue = string | number | Date | null | undefined;

function fmtChartValue(value: ChartValue | ChartValue[]): string {
  if (Array.isArray(value)) return value.map(fmtChartValue).join(", ");
  if (typeof value === "number") return fmtMetric(value);
  if (value instanceof Date) return value.toISOString();
  return value ?? "—";
}

function token(name: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

function points(series: MetricSeries): [number, number][] {
  if (series.xs.length !== series.ys.length) {
    throw new Error(`metric series ${series.id} has mismatched x/y values`);
  }
  return series.xs.map((x, index) => [x, series.ys[index] as number]);
}

function seriesOption(
  series: MetricSeries,
  data: [number, number][],
): EChartsCoreOption {
  const ink = token("--aibuildai-atlas-ink");
  const inkSecondary = token("--aibuildai-atlas-ink-secondary");
  const border = token("--aibuildai-atlas-border");
  const surface = token("--aibuildai-atlas-card");
  const primary = token("--aibuildai-highlight");
  const longSeries = series.xs.length > 400;
  return {
    animation: false,
    grid: { left: 46, right: 12, top: 14, bottom: longSeries ? 44 : 24 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", label: { backgroundColor: surface } },
      backgroundColor: surface,
      borderColor: border,
      textStyle: { color: ink, fontSize: 11 },
    },
    xAxis: {
      type: "value",
      name: series.x_kind,
      nameTextStyle: { color: inkSecondary, fontSize: 10 },
      min: "dataMin",
      max: "dataMax",
      axisLine: { lineStyle: { color: border } },
      axisLabel: { color: inkSecondary, fontSize: 10 },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: inkSecondary, fontSize: 10 },
      splitLine: { lineStyle: { color: border, opacity: 0.35 } },
    },
    dataZoom: longSeries
      ? [{ type: "slider", height: 16, bottom: 6, borderColor: border }]
      : undefined,
    series: [
      {
        id: series.id,
        name: series.name,
        type: "line",
        showSymbol: series.xs.length < 40,
        symbolSize: 4,
        lineStyle: { width: 1.8, color: primary },
        itemStyle: { color: primary },
        areaStyle: { opacity: 0.08, color: primary },
        tooltip: {
          valueFormatter: fmtChartValue,
        },
        data,
      },
    ],
  };
}

const DATA_PAGE_SIZE = 100;

function MetricDataTable(props: {
  series: MetricSeries;
  data: [number, number][];
}): ReactNode {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(props.data.length / DATA_PAGE_SIZE));
  const activePage = Math.min(page, pages - 1);
  useEffect(() => setPage(activePage), [activePage]);
  const rows = props.data.slice(
    activePage * DATA_PAGE_SIZE,
    (activePage + 1) * DATA_PAGE_SIZE,
  );
  return (
    <>
      <Button
        className="metric-data-toggle"
        onPress={() => setOpen((shown) => !shown)}
      >
        {open ? "Hide" : "View"} data
      </Button>
      {open && (
        <div className="metric-data">
          <div className="data-table-scroll">
            <table
              className="data-table metric-data-table"
              aria-label={`${props.series.name} data`}
            >
              <thead>
                <tr>
                  <th scope="col">{props.series.x_kind}</th>
                  <th scope="col">{props.series.name}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([x, y], index) => (
                  <tr key={`${x}:${activePage * DATA_PAGE_SIZE + index}`}>
                    <td>{fmtMetric(x)}</td>
                    <td>{fmtMetric(y)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <div className="metric-data-pages">
              <Button
                onPress={() => setPage(activePage - 1)}
                isDisabled={activePage === 0}
              >
                Previous
              </Button>
              <span className="num">
                {activePage + 1} / {pages}
              </span>
              <Button
                onPress={() => setPage(activePage + 1)}
                isDisabled={activePage === pages - 1}
              >
                Next
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}

export function MetricCurves(props: { series: MetricSeries[] }): ReactNode {
  const options = useMemo(
    () =>
      props.series.map((series) => {
        const data = points(series);
        return [series, data, seriesOption(series, data)] as const;
      }),
    [props.series],
  );
  if (options.length === 0) {
    return (
      <p className="quiet chart-empty">
        this WorkUnit has not recorded metrics yet
      </p>
    );
  }
  return (
    <div className="metric-curves">
      {options.map(([series, data, option]) => (
        <figure key={series.id} className="metric-curve">
          <figcaption className="metric-curve-name">
            {series.name}
            {series.segment > 1 && (
              <span className="metric-curve-segment num">
                t{series.segment}
              </span>
            )}
          </figcaption>
          <EChartsSurface option={option} height={170} />
          <MetricDataTable series={series} data={data} />
        </figure>
      ))}
    </div>
  );
}
