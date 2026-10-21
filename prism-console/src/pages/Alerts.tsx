import { Link } from "react-router-dom";
import { api } from "../api";
import { Loading, useAsync } from "../components";

export function AlertsPage() {
  const state = useAsync(() => api.alerts().then((payload) => payload.items), []);
  if (state.loading) return <Loading />;
  if (state.error) return <div className="error">{state.error}</div>;
  return (
    <section>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>告警中心</h2>
      <p style={{ color: "var(--muted)" }}>每條告警都帶觸發規則與原始網址。棄權事件不會出現在這裡。</p>
      {state.data?.length === 0 && <div className="empty">告警佇列是空的。</div>}
      <div className="grid">
        {state.data?.map((alert) => (
          <article className="panel" key={alert.alert_id}>
            <div className="row-actions">
              <span className="tag hot">{alert.priority.toFixed(3)}</span>
              <Link to={`/events/${alert.event_id}`}>{alert.summary}</Link>
            </div>
            <p>{alert.trigger_rules.map((rule) => <span className="tag" key={rule}>{rule}</span>)}</p>
            <ul>
              {alert.signal_urls.map((url) => <li key={url}><a href={url}>{url}</a></li>)}
            </ul>
          </article>
        ))}
      </div>
    </section>
  );
}
