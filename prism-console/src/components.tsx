import { useEffect, useState } from "react";
import type { ProbeHealth } from "./api";

export function Loading() {
  return (
    <div className="loading" role="status">
      <div className="prism" aria-hidden="true" />
      <div>白光正在穿過稜鏡，訊號仍在分解。</div>
    </div>
  );
}

export function Bridge({ probes }: { probes: ProbeHealth[] }) {
  return (
    <div className="bridge" title="橋面完整度。降級表示側橋啟用。">
      {probes.map((probe) => (
        <div key={probe.probe_id} className={`span ${probe.status}`} title={`${probe.probe_id} ${probe.detail}`}>
          <i />
        </div>
      ))}
    </div>
  );
}

export function formatBurst(value: number | null | undefined) {
  if (value == null) return "—";
  if (value > 99) return ">99";
  if (value < -99) return "<-99";
  return value.toFixed(1);
}

export function Meter({ value }: { value: number | null }) {
  const width = Math.max(0, Math.min(1, value ?? 0)) * 100;
  return (
    <div className="meter" aria-label={`優先級 ${value ?? 0}`}>
      <span style={{ width: `${width}%` }} />
    </div>
  );
}

const SPECTRUM = ["#e23d28", "#ef7d1a", "#e6b325", "#7da86a", "#3c8fba", "#5b62c7", "#9a5bd6"];

export function SpectrumGraph({ title, mentions }: { title: string; mentions: { mention_id: string; platform: string; near_duplicate: boolean }[] }) {
  const radius = 96;
  const cx = 210;
  const cy = 140;
  return (
    <svg className="graph" viewBox="0 0 420 280" role="img" aria-label="事件圖譜">
      <text x="16" y="24" fill="#b3a790" fontSize="12">規範事件為白點，提及為光譜色</text>
      {mentions.map((mention, index) => {
        const angle = -Math.PI / 2 + (Math.PI * 1.4 * index) / Math.max(mentions.length - 1, 1) - 0.7;
        const x = cx + Math.cos(angle) * radius;
        const y = cy + Math.sin(angle) * radius * 0.72;
        const color = SPECTRUM[index % SPECTRUM.length];
        return (
          <g key={mention.mention_id}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke={color} strokeOpacity={mention.near_duplicate ? 0.35 : 0.85} />
            <circle cx={x} cy={y} r="7" fill={color} />
            <text x={x + 10} y={y + 4} fill="#f3ecdf" fontSize="11">{mention.platform}</text>
          </g>
        );
      })}
      <circle cx={cx} cy={cy} r="16" fill="#f7f4ee" />
      <text x={cx} y={cy + 36} textAnchor="middle" fill="#f3ecdf" fontSize="12">{title.slice(0, 18)}</text>
    </svg>
  );
}

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    loader()
      .then((value) => {
        if (alive) {
          setData(value);
          setError(null);
        }
      })
      .catch((reason: Error) => {
        if (alive) setError(reason.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}

export function ConfirmAction({
  label,
  danger = false,
  onConfirm,
}: {
  label: string;
  danger?: boolean;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!open) {
    return (
      <button className={danger ? "btn danger" : "btn ghost"} onClick={() => setOpen(true)}>
        {label}
      </button>
    );
  }
  return (
    <div className="confirm">
      <input className="field" placeholder="理由（會寫入複核動作）" value={reason} onChange={(event) => setReason(event.target.value)} />
      {error && <div className="error">{error}</div>}
      <div className="row-actions">
        <button
          className="btn"
          onClick={async () => {
            try {
              await onConfirm(reason);
              setOpen(false);
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "操作失敗");
            }
          }}
        >
          確認執行
        </button>
        <button className="btn ghost" onClick={() => setOpen(false)}>取消</button>
      </div>
    </div>
  );
}
