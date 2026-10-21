import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Loading, Meter, useAsync } from "../components";

export function EventsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("active");
  const [minPriority, setMinPriority] = useState("");
  const [entity, setEntity] = useState("");
  const query = `?status=${encodeURIComponent(status)}${minPriority ? `&min_priority=${encodeURIComponent(minPriority)}` : ""}${entity ? `&entity=${encodeURIComponent(entity)}` : ""}`;
  const state = useAsync(() => api.events(query).then((payload) => payload.items), [query]);
  return (
    <section>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>事件</h2>
      <div className="filters" style={{ marginBottom: 12 }}>
        <select className="field" style={{ width: 140 }} value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="active">活躍</option>
          <option value="merged">已歸併</option>
          <option value="split">已拆分</option>
          <option value="stale">陳舊</option>
        </select>
        <input className="field" style={{ width: 160 }} placeholder="最低優先級" value={minPriority} onChange={(event) => setMinPriority(event.target.value)} />
        <input className="field" style={{ width: 180 }} placeholder="實體" value={entity} onChange={(event) => setEntity(event.target.value)} />
      </div>
      {state.loading && <Loading />}
      {state.error && <div className="error">{state.error}</div>}
      {state.data && state.data.length === 0 && <div className="empty">這個篩選下沒有事件。</div>}
      {state.data && state.data.length > 0 && (
        <table>
          <thead>
            <tr><th>標題</th><th>優先級</th><th>置信</th><th>提及</th><th>實體</th><th>狀態</th></tr>
          </thead>
          <tbody>
            {state.data.map((item) => (
              <tr className="click" key={item.event_id} onClick={() => navigate(`/events/${item.event_id}`)}>
                <td>{item.title}{item.abstain && <span className="tag bad">棄權</span>}</td>
                <td style={{ width: 140 }}><Meter value={item.priority} /><div style={{ fontSize: 12, color: "var(--faint)" }}>{item.priority?.toFixed(3) ?? "—"}</div></td>
                <td>{item.calibrated_confidence?.toFixed(3) ?? "—"}</td>
                <td>{item.mention_count}</td>
                <td>{item.entities.join("、") || "—"}</td>
                <td>{item.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
