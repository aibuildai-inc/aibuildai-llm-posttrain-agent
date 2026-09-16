// The one ECharts lifecycle adapter (official `echarts` package, no
// wrapper library). Named imports keep only the line chart, the three
// components the metric curves use, and the canvas renderer. The
// parent loads this module as one async chunk. The instance is created
// once per mounted surface. Updates use stable series ids through setOption (no
// chart recreation on a poll), a ResizeObserver drives resize() when the
// Inspector width changes, and unmount disposes the instance.
import { useEffect, useRef, type ReactNode } from "react";
import { LineChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsCoreOption, ECharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

export function EChartsSurface(props: {
  option: EChartsCoreOption;
  height: number;
}): ReactNode {
  const host = useRef<HTMLDivElement | null>(null);
  const chart = useRef<ECharts | null>(null);
  const { option } = props;
  const initialOption = useRef(option);
  initialOption.current = option;

  useEffect(() => {
    const element = host.current;
    if (element === null) return;
    chart.current = echarts.init(element);
    chart.current.setOption(initialOption.current);
    const observer = new ResizeObserver(() => chart.current?.resize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
    // The instance is created exactly once per mount; data updates go
    // through the setOption effect below (the initial option is read from
    // a ref so this effect owns only the chart lifecycle).
  }, []);

  useEffect(() => {
    chart.current?.setOption(option);
  }, [option]);

  return (
    <div
      ref={host}
      className="chart-surface"
      aria-hidden="true"
      style={{ height: props.height }}
    />
  );
}
