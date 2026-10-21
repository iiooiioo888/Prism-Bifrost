import { useState } from "react";
import { api, currentRole, type Brief } from "../api";
import { Loading, useAsync } from "../components";

export function BriefsPage() {
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<Brief | null>(null);
  const [error, setError] = useState<string | null>(null);
  const state = useAsync(() => api.briefs().then((payload) => payload.items), [tick]);
  const canWrite = currentRole() !== "viewer";
  if (state.loading) return <Loading />;
  return (
    <section className="grid two">
      <div>
        <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>簡報</h2>
        {canWrite && (
          <div className="row-actions" style={{ marginBottom: 12 }}>
            <button className="btn" onClick={async () => { try { const brief = await api.createBrief("day"); setSelected(brief); setTick((value) => value + 1); } catch (caught) { setError(caught instanceof Error ? caught.message : "生成失敗"); } }}>生成日報</button>
            <button className="btn ghost" onClick={async () => { try { const brief = await api.createBrief("week"); setSelected(brief); setTick((value) => value + 1); } catch (caught) { setError(caught instanceof Error ? caught.message : "生成失敗"); } }}>生成週報</button>
          </div>
        )}
        {error && <div className="error">{error}</div>}
        {state.error && <div className="error">{state.error}</div>}
        {state.data?.length === 0 && <div className="empty">還沒有簡報快照。</div>}
        {state.data?.map((brief) => (
          <button key={brief.brief_id} className="btn ghost" style={{ display: "block", width: "100%", textAlign: "left", marginBottom: 8 }} onClick={() => setSelected(brief)}>
            {brief.period === "day" ? "日報" : "週報"}　{brief.created_at}　{brief.model_assisted ? "模型輔助" : "人工基準"}
          </button>
        ))}
      </div>
      <article className="panel">
        <h3>快照</h3>
        {selected ? <div className="brief">{selected.body}</div> : <div className="empty">選擇一期簡報以查看證據快照。</div>}
      </article>
    </section>
  );
}
