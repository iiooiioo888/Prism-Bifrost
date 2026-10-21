import { Link } from "react-router-dom";
import { useState } from "react";
import { api, currentRole } from "../api";
import { ConfirmAction, Loading, useAsync } from "../components";

export function ReviewPage() {
  const [tick, setTick] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const state = useAsync(() => api.queue().then((payload) => payload.suggestions), [tick]);
  const canWrite = currentRole() !== "viewer";
  if (state.loading) return <Loading />;
  if (state.error) return <div className="error">{state.error}</div>;
  return (
    <section>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>複核佇列</h2>
      <p style={{ color: "var(--muted)" }}>中置信建議停在這裡。採納才會改寫事實層，拒絕只留下研判記錄。</p>
      {message && <div className="empty">{message}</div>}
      {state.data?.length === 0 && <div className="empty">佇列已清空。</div>}
      {state.data?.map((item) => (
        <article className="panel" key={item.suggestion_id} style={{ marginBottom: 12 }}>
          <div className="row-actions">
            <span className="tag hot">{item.band}　{item.score.toFixed(3)}</span>
            <span className="tag">{item.source}</span>
          </div>
          <p>
            <Link to={`/events/${item.left_event_id}`}>保留事件</Link>
            {"　←　"}
            <Link to={`/events/${item.right_event_id}`}>待歸併事件</Link>
          </p>
          {canWrite && (
            <div className="row-actions">
              <ConfirmAction
                label="採納歸併"
                onConfirm={async (reason) => {
                  await api.accept(item.suggestion_id, reason || "人工確認為同一事件");
                  setMessage("已採納，事實層關聯已更新。");
                  setTick((value) => value + 1);
                }}
              />
              <button className="btn danger" onClick={async () => {
                await api.reject(item.suggestion_id, "維持分開");
                setMessage("已拒絕，兩邊事件維持原狀。");
                setTick((value) => value + 1);
              }}>拒絕</button>
            </div>
          )}
        </article>
      ))}
    </section>
  );
}
