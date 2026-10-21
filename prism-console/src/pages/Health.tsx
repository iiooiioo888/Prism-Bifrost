import { api } from "../api";
import { Loading, useAsync } from "../components";

const STATUS_TEXT = { healthy: "健康", degraded: "降級", down: "中斷" };

export function HealthPage() {
  const state = useAsync(async () => {
    const [probes, proxies] = await Promise.all([api.probes(), api.proxies()]);
    return { probes: probes.items, proxies };
  }, []);
  if (state.loading) return <Loading />;
  if (state.error) return <div className="error">{state.error}</div>;
  const proxies = state.data?.proxies;
  return (
    <section>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>探針健康</h2>
      <p style={{ color: "var(--muted)" }}>側橋啟用會顯示為降級，不會被靜默歸零。失敗率加速上升時提前走備選。未啟用的登入態通道保持關閉。</p>
      <table>
        <thead>
          <tr><th>探針</th><th>狀態</th><th>備選比例</th><th>連續失敗</th><th>劣化速率</th><th>提前備選</th><th>最近成功</th><th>說明</th></tr>
        </thead>
        <tbody>
          {state.data?.probes.map((probe) => (
            <tr key={probe.probe_id}>
              <td>{probe.probe_id}</td>
              <td><span className={`tag ${probe.status === "healthy" ? "ok" : probe.status === "degraded" ? "hot" : "bad"}`}>{STATUS_TEXT[probe.status]}</span></td>
              <td>{probe.fallback_ratio_24h.toFixed(2)}</td>
              <td>{probe.consecutive_failures}</td>
              <td>{probe.degradation_rate == null ? "—" : probe.degradation_rate.toFixed(2)}</td>
              <td>{probe.preempt_fallback ? "是" : "否"}</td>
              <td>{probe.last_success_at || "—"}</td>
              <td>{probe.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500, marginTop: 28 }}>出口代理</h2>
      <p style={{ color: "var(--muted)" }}>
        代理池{proxies?.enabled ? "已啟用" : "預設關閉"}。只在出口傳輸失敗時改走下一條。目標拒絕、驗證碼或頻率限制會停止，不會換 IP 再試。
      </p>
      <table>
        <thead>
          <tr><th>代理</th><th>狀態</th><th>連續傳輸失敗</th><th>最近結果</th><th>說明</th></tr>
        </thead>
        <tbody>
          {proxies?.items.map((proxy) => (
            <tr key={proxy.proxy_id}>
              <td>{proxy.proxy_id}</td>
              <td><span className={`tag ${proxy.status === "healthy" ? "ok" : proxy.status === "degraded" ? "hot" : "bad"}`}>{STATUS_TEXT[proxy.status]}</span></td>
              <td>{proxy.consecutive_transport_failures}</td>
              <td>{proxy.last_outcome || "—"}</td>
              <td>{proxy.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
