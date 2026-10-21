import { Link } from "react-router-dom";
import { useState } from "react";
import { api, currentRole } from "../api";
import { ConfirmAction, Loading, useAsync } from "../components";

export function AbstainPage() {
  const [tick, setTick] = useState(0);
  const state = useAsync(() => api.events("?abstain=true").then((payload) => payload.items), [tick]);
  const canWrite = currentRole() !== "viewer";
  if (state.loading) return <Loading />;
  if (state.error) return <div className="error">{state.error}</div>;
  return (
    <section>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>棄權</h2>
      <p style={{ color: "var(--muted)" }}>校準置信度低於閾值的事件留在庫裡，但不進入告警與簡報。人工可以提升。</p>
      {state.data?.length === 0 && <div className="empty">目前沒有棄權事件。</div>}
      {state.data?.map((item) => (
        <article className="panel" key={item.event_id} style={{ marginBottom: 10 }}>
          <Link to={`/events/${item.event_id}`}>{item.title}</Link>
          <div style={{ color: "var(--faint)", fontSize: 13 }}>置信 {item.calibrated_confidence?.toFixed(3)}　提及 {item.mention_count}</div>
          {canWrite && (
            <ConfirmAction
              label="提升回研判"
              onConfirm={async (reason) => {
                await api.promote(item.event_id, reason || "人工提升");
                setTick((value) => value + 1);
              }}
            />
          )}
        </article>
      ))}
    </section>
  );
}
