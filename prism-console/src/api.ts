export type Role = "viewer" | "analyst" | "admin";

const TOKENS: Record<Role, string> = {
  viewer: "dev-viewer",
  analyst: "dev-analyst",
  admin: "dev-admin",
};

const ROLE_KEY = "prism-role";

export function currentRole(): Role {
  const stored = localStorage.getItem(ROLE_KEY);
  if (stored === "viewer" || stored === "analyst" || stored === "admin") return stored;
  return "analyst";
}

export function setRole(role: Role) {
  localStorage.setItem(ROLE_KEY, role);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}, confirm = false): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${TOKENS[currentRole()]}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (confirm) headers.set("X-Confirm", "yes");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      /* 保留狀態文字 */
    }
    throw new ApiError(response.status, String(detail));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  events: (query = "") => request<{ items: EventSummary[] }>(`/api/v1/events${query}`),
  event: (id: string) => request<EventDetail>(`/api/v1/events/${id}`),
  mentions: (id: string) => request<{ items: Mention[] }>(`/api/v1/events/${id}/mentions`),
  metrics: (id: string) => request<{ items: Metric[] }>(`/api/v1/events/${id}/metrics`),
  alerts: () => request<{ items: Alert[] }>("/api/v1/alerts"),
  probes: () => request<{ items: ProbeHealth[] }>("/api/v1/health/probes"),
  proxies: () => request<ProxyPoolStatus>("/api/v1/health/proxies"),
  scenes: () => request<{ items: SceneConfig[] }>("/api/v1/scenes"),
  sceneDecisions: (sceneId: string) => request<{ items: SceneDecision[] }>(`/api/v1/scenes/${sceneId}/decisions`),
  dashboard: () => request<Dashboard>("/api/v1/dashboard"),
  briefs: () => request<{ items: Brief[] }>("/api/v1/briefs"),
  brief: (id: string) => request<Brief>(`/api/v1/briefs/${id}`),
  createBrief: (period: "day" | "week") =>
    request<Brief>("/api/v1/briefs", { method: "POST", body: JSON.stringify({ period }) }),
  queue: () => request<{ suggestions: Suggestion[] }>("/api/v1/review/queue"),
  label: (id: string, label: string, note: string) =>
    request(`/api/v1/mentions/${id}/label`, { method: "POST", body: JSON.stringify({ label, note }) }),
  accept: (id: string, reason: string) =>
    request(`/api/v1/review/suggestions/${id}/accept`, { method: "POST", body: JSON.stringify({ reason }) }, true),
  reject: (id: string, reason: string) =>
    request(`/api/v1/review/suggestions/${id}/reject`, { method: "POST", body: JSON.stringify({ reason }) }),
  promote: (id: string, reason: string) =>
    request(`/api/v1/events/${id}/promote`, { method: "POST", body: JSON.stringify({ reason }) }, true),
  split: (id: string, mentionIds: string[], reason: string, title: string) =>
    request<{ event_id: string }>(
      `/api/v1/events/${id}/split`,
      { method: "POST", body: JSON.stringify({ mention_ids: mentionIds, reason, title }) },
      true,
    ),
  merge: (targetId: string, sourceId: string, reason: string) =>
    request(
      `/api/v1/events/${targetId}/merge`,
      { method: "POST", body: JSON.stringify({ source_event_id: sourceId, reason }) },
      true,
    ),
};

export interface EventSummary {
  event_id: string;
  title: string;
  status: string;
  priority: number | null;
  calibrated_confidence: number | null;
  abstain: boolean;
  burst_score: number | null;
  mention_count: number;
  entities: string[];
  promoted: boolean;
  first_seen: string;
  last_updated: string;
}

export interface Mention {
  mention_id: string;
  event_id: string | null;
  source_url: string;
  upstream_timestamp: string | null;
  platform: string;
  author: string | null;
  title: string | null;
  body: string;
  acquisition_mode: string;
  source_type: string;
  near_duplicate: boolean;
  link_method: string | null;
  link_confidence: number | null;
  labels: { label: string; note: string | null; actor: string }[];
  raw_ref: string;
}

export interface Metric {
  window_start: string;
  window_end: string;
  mention_count: number;
  source_diversity: number | null;
  platform_entropy: number | null;
  diffusion_rate: number | null;
  sentiment_dist: Record<string, number>;
  burst_score: number | null;
}

export interface EventDetail extends Omit<EventSummary, "entities"> {
  mentions: Mention[];
  metrics: Metric[];
  components: Record<string, unknown>;
  suggestions: Suggestion[];
  entities: { entity_id: string; name: string; role: string; confidence: number; link_method: string }[];
  narratives: { label: string; summary: string; mention_ids: string[]; unsupported: boolean; model: string }[];
  provenance: Record<string, unknown>;
}

export interface Suggestion {
  suggestion_id: string;
  left_event_id: string;
  right_event_id: string;
  score: number;
  band: string;
  source: string;
  status: string;
  payload?: Record<string, unknown>;
}

export interface Alert {
  alert_id: string;
  event_id: string;
  priority: number;
  trigger_rules: string[];
  calibrated_confidence: number;
  signal_urls: string[];
  summary: string;
  created_at: string;
}

export interface ProxyEndpointStatus {
  proxy_id: string;
  url: string;
  enabled: boolean;
  status: "healthy" | "degraded" | "down";
  detail: string;
  consecutive_transport_failures: number;
  last_outcome: string | null;
  last_success_at: string | null;
}

export interface ProxyPoolStatus {
  enabled: boolean;
  retry_on_access_denied: boolean;
  items: ProxyEndpointStatus[];
}

export interface ProbeHealth {
  probe_id: string;
  platform: string;
  status: "healthy" | "degraded" | "down";
  detail: string;
  last_success_at: string | null;
  consecutive_failures: number;
  fallback_ratio_24h: number;
  enabled: boolean;
  degradation_rate: number | null;
  preempt_fallback: boolean;
}

export interface SceneConfig {
  scene_id: string;
  title: string;
  fusion: "canonical_event" | "sku_offer";
  dimensions: {
    subjects: string[];
    signal_domains: string[];
    triggers: string[];
    delivery: string[];
    time_sensitivity: string;
  };
  compliance: Record<string, boolean>;
  baseline_days_required: number;
  price_rule_order: string[];
}

export interface RankedOffer {
  offer_id: string;
  platform: string;
  source_url: string;
  list_price: string;
  payable: string;
  score: string;
  match_band: string;
  price_basis: string;
  price_protection: boolean;
  reasons: string[];
  steps: { layer: string; label: string; base_before: string; deduction: string; payable_after: string }[];
}

export interface PricePayload {
  sku_code: string;
  sku_title: string;
  node: string;
  path: string;
  label: string;
  baseline_days: number;
  baseline_required: number;
  baseline_median: string | null;
  baseline_median_provisional: boolean;
  confidence_reduced: boolean;
  uncertainties: string[];
  ranking: RankedOffer[];
  withheld: { platform: string; reason: string; price_basis: string; match_band: string }[];
  abstain: {
    executable: { statement: string; payable: string; source_url: string }[];
    confirmations: string[];
    risks: string[];
    prohibited: string[];
  };
}

export interface SceneDecision {
  decision_id: string;
  node: string;
  path: string;
  created_at: string | null;
  payload: PricePayload;
}

export interface Dashboard {
  mention_series: { t: string; count: number }[];
  confidence_histogram: Record<string, number>;
  burst_events: { event_id: string; title: string; priority: number; burst_score: number }[];
  priority_ranking: { event_id: string; title: string; priority: number; burst_score: number; abstain: boolean }[];
  probes: ProbeHealth[];
}

export interface Brief {
  brief_id: string;
  period: string;
  body: string;
  model_assisted: boolean;
  created_at: string;
  evidence: { accepted: unknown[]; rejected: unknown[] };
}
