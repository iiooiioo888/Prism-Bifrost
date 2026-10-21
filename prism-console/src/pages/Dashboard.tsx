import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import * as echarts from "echarts";
import { api } from "../api";
import { Loading, formatBurst, useAsync } from "../components";

export function DashboardPage() {
  const state = useAsync(() => api.dashboard(), []);
  const seriesRef = useRef<HTMLDivElement>(null);
  const histRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!state.data || !seriesRef.current || !histRef.current) return;
    const series = echarts.init(seriesRef.current);
    const hist = echarts.init(histRef.current);
    const text = "#b3a790";
    series.setOption({
      backgroundColor: "transparent",
      grid: { left: 36, right: 12, top: 20, bottom: 28 },
      xAxis: { type: "category", data: state.data.mention_series.map((item) => item.t.slice(11, 16)), axisLabel: { color: text } },
      yAxis: { type: "value", axisLabel: { color: text }, splitLine: { lineStyle: { color: "rgba(236,224,198,0.08)" } } },
      series: [{ type: "line", smooth: true, showSymbol: false, data: state.data.mention_series.map((item) => item.count), lineStyle: { color: "#e6b325" }, areaStyle: { color: "rgba(230,179,37,0.15)" } }],
    });
    const bands = Object.entries(state.data.confidence_histogram);
    hist.setOption({
      backgroundColor: "transparent",
      grid: { left: 36, right: 12, top: 20, bottom: 28 },
      xAxis: { type: "category", data: bands.map(([name]) => name), axisLabel: { color: text, fontSize: 10 } },
      yAxis: { type: "value", axisLabel: { color: text }, splitLine: { lineStyle: { color: "rgba(236,224,198,0.08)" } } },
      series: [{ type: "bar", data: bands.map(([, value]) => value), itemStyle: { color: "#3c8fba" } }],
    });
    const onResize = () => { series.resize(); hist.resize(); };
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); series.dispose(); hist.dispose(); };
  }, [state.data]);
  if (state.loading) return <Loading />;
  if (state.error) return <div className="error">{state.error}</div>;
  if (!state.data) return null;
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div>
        <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500, margin: "0 0 6px" }}>態勢</h2>
        <p style={{ color: "var(--muted)", margin: 0 }}>提及量、置信分布、突發與優先級。降級通道不會被畫成零。</p>
      </div>
      <div className="grid two">
        <section className="panel">
          <h3>二十四小時提及量</h3>
          <div ref={seriesRef} className="chart" />
        </section>
        <section className="panel">
          <h3>校準置信度分布</h3>
          <div ref={histRef} className="chart" />
        </section>
      </div>
      <div className="grid two">
        <section className="panel">
          <h3>優先級排序</h3>
          {state.data.priority_ranking.length === 0 && <div className="empty">還沒有可排序的事件。可先執行 prism demo seed。</div>}
          {state.data.priority_ranking.map((item) => (
            <div key={item.event_id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
              <Link to={`/events/${item.event_id}`}>{item.title}</Link>
              <div style={{ color: "var(--faint)", fontSize: 12 }}>優先級 {item.priority?.toFixed(3)}　突發 {formatBurst(item.burst_score)}</div>
            </div>
          ))}
        </section>
        <section className="panel">
          <h3>突發候選</h3>
          {state.data.burst_events.length === 0 && <div className="empty">目前沒有超過閾值的突發。</div>}
          {state.data.burst_events.map((item) => (
            <div key={item.event_id} style={{ padding: "8px 0" }}>
              <span className="tag hot">突發</span> <Link to={`/events/${item.event_id}`}>{item.title}</Link>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
