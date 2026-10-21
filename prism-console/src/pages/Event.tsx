import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, currentRole } from "../api";
import { ConfirmAction, Loading, SpectrumGraph, formatBurst, useAsync } from "../components";

const LABEL_TEXT: Record<string, string> = { confirm: "確認", exclude: "排除", uncertain: "存疑" };

export function EventPage() {
  const { eventId = "" } = useParams();
  const [tick, setTick] = useState(0);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const state = useAsync(() => api.event(eventId), [eventId, tick]);
  const canWrite = currentRole() !== "viewer";
  if (state.loading) return <Loading />;
  if (state.error) return <div className="error">{state.error}</div>;
  if (!state.data) return null;
  const event = state.data;
  const hour = event.metrics.find((item) => item.burst_score !== null) ?? event.metrics[0];
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div>
        <Link to="/events" style={{ color: "var(--faint)" }}>返回事件</Link>
        <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500, margin: "8px 0" }}>{event.title}</h2>
        <div className="row-actions">
          <span className="tag">{event.status}</span>
          {event.abstain && <span className="tag bad">棄權</span>}
          {(event.burst_score ?? 0) > 3 && <span className="tag hot" title={String(event.burst_score)}>突發 {formatBurst(event.burst_score)}</span>}
          <span className="tag">優先級 {event.priority?.toFixed(3) ?? "—"}</span>
        </div>
      </div>
      {message && <div className="empty">{message}</div>}
      <div className="grid two">
        <section className="panel">
          <h3>圖譜</h3>
          <SpectrumGraph title={event.title} mentions={event.mentions} />
        </section>
        <section className="panel">
          <h3>評分明細</h3>
          <div className="kv">
            {Object.entries(event.components || {}).map(([key, value]) => (
              <div key={key} style={{ display: "contents" }}>
                <span>{key}</span>
                <div>{typeof value === "number" ? value.toFixed(3) : String(value ?? "—")}</div>
              </div>
            ))}
          </div>
          {hour && (
            <p style={{ color: "var(--muted)", fontSize: 13 }}>
              最近窗口提及 {hour.mention_count}，來源多樣性 {hour.source_diversity?.toFixed(2) ?? "—"}，平台熵 {hour.platform_entropy?.toFixed(2) ?? "—"}
            </p>
          )}
          <h3>實體</h3>
          {event.entities.length === 0 && <div className="empty">尚未以規則連結實體。</div>}
          {event.entities.map((entity) => (
            <div key={entity.entity_id}>{entity.name}　{entity.link_method}　{entity.confidence.toFixed(2)}</div>
          ))}
        </section>
      </div>
      <section className="panel">
        <h3>提及與引用</h3>
        {event.mentions.map((mention, index) => (
          <article className="mention" key={mention.mention_id}>
            <div>
              <span className="swatch" style={{ background: ["#e23d28", "#ef7d1a", "#e6b325", "#7da86a", "#3c8fba", "#5b62c7", "#9a5bd6"][index % 7] }} />
              {mention.platform}
              <div style={{ color: "var(--faint)", fontSize: 12 }}>{mention.acquisition_mode === "fallback" ? "備選" : "正常"}</div>
              {mention.near_duplicate && <div className="tag">近似轉載</div>}
            </div>
            <div>
              <strong>{mention.title}</strong>
              <div style={{ color: "var(--muted)", margin: "4px 0" }}>{mention.body}</div>
              <a href={mention.source_url}>{mention.source_url}</a>
              <div style={{ color: "var(--faint)", fontSize: 12, marginTop: 4 }}>
                {mention.author || "佚名"}　{mention.upstream_timestamp || "時間缺失"}　原始回應 {mention.raw_ref}
                {mention.link_method && `　連結 ${mention.link_method}`}
              </div>
              {mention.labels.length > 0 && <div style={{ marginTop: 6 }}>{mention.labels.map((label) => <span className="tag" key={label.label}>{LABEL_TEXT[label.label] || label.label} {label.note}</span>)}</div>}
              {canWrite && (
                <div className="row-actions" style={{ marginTop: 8 }}>
                  <input className="field" style={{ maxWidth: 280 }} placeholder="筆記" value={notes[mention.mention_id] ?? ""} onChange={(input) => setNotes((current) => ({ ...current, [mention.mention_id]: input.target.value }))} />
                  {(["confirm", "uncertain", "exclude"] as const).map((label) => (
                    <button
                      key={label}
                      className="btn ghost"
                      onClick={async () => {
                        await api.label(mention.mention_id, label, notes[mention.mention_id] ?? "");
                        setMessage(`已標註為${LABEL_TEXT[label]}，事實層關聯未改寫。`);
                        setTick((value) => value + 1);
                      }}
                    >
                      {LABEL_TEXT[label]}
                    </button>
                  ))}
                  <ConfirmAction
                    danger
                    label="拆出此提及"
                    onConfirm={async (reason) => {
                      const created = await api.split(event.event_id, [mention.mention_id], reason || notes[mention.mention_id] || "", `${mention.title || "提及"}（拆分）`);
                      setMessage(`已拆出新事件 ${created.event_id}`);
                      setTick((value) => value + 1);
                    }}
                  />
                </div>
              )}
            </div>
          </article>
        ))}
      </section>
      {event.narratives.length > 0 && (
        <section className="panel">
          <h3>敘事標籤</h3>
          {event.narratives.map((item) => (
            <p key={item.label}>{item.label}：{item.summary}{item.unsupported && <span className="tag bad">無支撐</span>}</p>
          ))}
        </section>
      )}
    </div>
  );
}
