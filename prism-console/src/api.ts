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
  marketplaces: () => request<{ items: Marketplace[] }>("/api/v1/marketplaces"),
  readListing: (sceneId: string, url: string) =>
    request<ProductListing>(`/api/v1/scenes/${encodeURIComponent(sceneId)}/listings`, {
      method: "POST",
      body: JSON.stringify({ url }),
    }),
  monitorProduct: (sceneId: string, query: string) =>
    request<ProductMonitorReport>(`/api/v1/scenes/${encodeURIComponent(sceneId)}/monitors`, {
      method: "POST",
      body: JSON.stringify({ query }),
    }),
  sceneDecisions: (sceneId: string) => request<{ items: SceneDecision[] }>(`/api/v1/scenes/${sceneId}/decisions`),
  productImages: (sceneId: string, skuCode: string) =>
    request<{ sku_code: string; items: ProductImage[] }>(
      `/api/v1/scenes/${sceneId}/images?sku_code=${encodeURIComponent(skuCode)}`,
    ),
  productImageBlob: async (imageId: string) => {
    const headers = new Headers();
    headers.set("Authorization", `Bearer ${TOKENS[currentRole()]}`);
    const response = await fetch(`/api/v1/product-images/${imageId}`, { headers });
    if (!response.ok) throw new ApiError(response.status, response.statusText);
    return response.blob();
  },
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

export interface MonitorItem {
  title: string | null;
  source_url: string | null;
  list_price: string | null;
  status: "priced" | "withheld" | "blocked" | "empty";
  detail: string;
  sku_code?: string | null;
  match_confidence?: string | null;
  match_band?: "high" | "medium" | "low" | null;
  availability?: string | null;
  shipping_price?: string | null;
  seller?: string | null;
}

export interface ComparisonRow {
  platform: string;
  label: string;
  source_url: string;
  title: string;
  list_price: string;
  payable: string | null;
  shipping: string | null;
  shipping_known: boolean;
  availability: string;
  seller: string | null;
  match_band: string;
  match_confidence: string;
  gap: string | null;
  unit_price: string | null;
  unit: string | null;
  baseline: string | null;
  comparable: boolean;
  review: boolean;
  reason: string;
}

export interface ComparisonGroup {
  sku_code: string;
  title: string;
  comparable_count: number;
  spread: string | null;
  low_payable: string | null;
  low_platform: string | null;
  statement: string;
  review: string | null;
  rows: ComparisonRow[];
}

export interface RelatedSpecRow {
  sku_code: string;
  platform: string;
  label: string;
  source_url: string;
  capacity: string | null;
  color: string | null;
  list_price: string;
  payable: string;
  unit_price: string | null;
  unit: string | null;
  match_band: string;
}

export interface PriceComparison {
  note: string;
  same_identity: ComparisonGroup[];
  related_specs: Array<{ brand: string; model: string; note: string; rows: RelatedSpecRow[] }>;
}

export interface PriceDropNotice {
  sku_code: string;
  source_url: string;
  previous_price: string;
  list_price: string;
  detail: string;
}

export interface MonitorPlatform {
  platform: string;
  label: string;
  status: "priced" | "withheld" | "blocked" | "empty";
  detail: string;
  items: MonitorItem[];
}

export interface ProductMonitorReport {
  monitor_id: string;
  run_id: string;
  scene_id: string;
  query: string;
  sku_code: string;
  match_confidence: string;
  event_created: boolean;
  detail: string;
  schedules?: string[];
  official?: { configured: boolean; detail: string };
  alerts?: PriceDropNotice[];
  recommendations?: Array<{ label?: string; abstain?: { executable?: Array<{ statement: string }> } }>;
  comparison?: PriceComparison;
  platforms: MonitorPlatform[];
}

export interface Marketplace {
  platform: string;
  label: string;
  hosts: string[];
  search_host: string;
}

export interface ProductListing {
  offer_id: string;
  scene_id: string;
  platform: string;
  label: string;
  sku_code: string;
  title: string;
  source_url: string;
  list_price: string | null;
  price_basis: "guest" | "logged_in";
  availability?: string;
  shipping_price?: string | null;
  seller?: string | null;
  match_confidence: string;
  stored: boolean;
  event_created: boolean;
  detail: string;
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
  shipping?: string | null;
  score: string;
  match_band: string;
  match_cap: string;
  match_limits_conclusion: boolean;
  price_basis: string;
  price_protection: boolean;
  price_protection_verdict: {
    applies: boolean;
    trigger: string;
    matched_exclusions: string[];
    unparsed: boolean;
    detail: string;
  };
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
  sku_match: { limits_conclusion: boolean; conclusion_confidence_cap: string | null };
  ranking: RankedOffer[];
  withheld: { platform: string; reason: string; price_basis: string; match_band: string }[];
  abstain: {
    executable: { statement: string; payable: string; source_url: string }[];
    confirmations: string[];
    risks: string[];
    prohibited: string[];
  };
}

export interface ProductImage {
  image_id: string;
  sku_code: string;
  source_page_url: string;
  image_url: string;
  content_type: string;
  byte_size: number;
  sha256: string;
  acquisition_mode: string;
  collected_at: string | null;
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
