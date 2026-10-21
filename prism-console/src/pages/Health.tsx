import { api } from "../api";
import { Loading, useAsync } from "../components";

const STATUS_TEXT = { healthy: "健康", degraded: "降級", down: "中斷" };

export function HealthPage() {
  const state = useAsync(() => api.probes().then((payload) => payload.items), []);
  if (state.loading) return <Loading />;
  if (state.error) return <div className="error">{state.error}</div>;
  return (
    <section>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>探針健康</h2>
      <p style={{ color: "var(--muted)" }}>側橋啟用會顯示為降級，不會被靜默歸零。未啟用的登入態通道保持關閉。</p>
      <table>
        <thead>
          <tr><th>探針</th><th>狀態</th><th>備選比例</th><th>連續失敗</th><th>最近成功</th><th>說明</th></tr>
        </thead>
        <tbody>
          {state.data?.map((probe) => (
            <tr key={probe.probe_id}>
              <td>{probe.probe_id}</td>
              <td><span className={`tag ${probe.status === "healthy" ? "ok" : probe.status === "degraded" ? "hot" : "bad"}`}>{STATUS_TEXT[probe.status]}</span></td>
              <td>{probe.fallback_ratio_24h.toFixed(2)}</td>
              <td>{probe.consecutive_failures}</td>
              <td>{probe.last_success_at || "—"}</td>
              <td>{probe.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
