-- 稜鏡（Prism Bifröst）資料庫定義
-- 事實層與研判層分表。PostgreSQL 16。
-- 事件指標只由確定性管道寫入。審計日誌僅追加。

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE canonical_event (
  event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type      TEXT NOT NULL,
  title           TEXT NOT NULL,
  first_seen      TIMESTAMPTZ NOT NULL,
  last_updated    TIMESTAMPTZ NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','stale','merged','split')),
  merged_from     UUID[] DEFAULT '{}',
  provenance      JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE event_mention (
  mention_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id            UUID REFERENCES canonical_event(event_id),
  source_url          TEXT NOT NULL,
  upstream_timestamp  TIMESTAMPTZ,
  collected_at        TIMESTAMPTZ NOT NULL,
  platform            TEXT NOT NULL,
  author              TEXT,
  title               TEXT,
  body                TEXT NOT NULL,
  language            TEXT NOT NULL,
  acquisition_mode    TEXT NOT NULL CHECK (acquisition_mode IN ('live','fallback')),
  raw_ref             TEXT NOT NULL,
  content_hash        TEXT NOT NULL,
  duplicate_of        UUID REFERENCES event_mention(mention_id),
  link_method         TEXT CHECK (link_method IN ('rule','semantic','human')),
  link_confidence     NUMERIC(4,3),
  source_type         TEXT NOT NULL DEFAULT 'web',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_mention_event ON event_mention(event_id);
CREATE INDEX idx_mention_time ON event_mention(upstream_timestamp);
CREATE INDEX idx_mention_hash ON event_mention(content_hash);

CREATE TABLE event_metrics (
  event_id        UUID NOT NULL REFERENCES canonical_event(event_id),
  window_start    TIMESTAMPTZ NOT NULL,
  window_end      TIMESTAMPTZ NOT NULL,
  mention_count   INTEGER NOT NULL,
  source_diversity NUMERIC(5,4),
  platform_entropy NUMERIC(6,4),
  diffusion_rate  NUMERIC(8,4),
  sentiment_dist  JSONB,
  burst_score     NUMERIC(8,3),
  computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, window_start, window_end)
);

-- 研判層。寫入這些表不會自動改寫 event_mention.event_id。
CREATE TABLE merge_suggestion (
  suggestion_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  left_event_id     UUID NOT NULL REFERENCES canonical_event(event_id),
  right_event_id    UUID NOT NULL REFERENCES canonical_event(event_id),
  left_mention_id   UUID NOT NULL REFERENCES event_mention(mention_id),
  right_mention_id  UUID NOT NULL REFERENCES event_mention(mention_id),
  score             NUMERIC(6,4) NOT NULL,
  band              TEXT NOT NULL CHECK (band IN ('high','medium','low')),
  source            TEXT NOT NULL,
  payload           JSONB NOT NULL DEFAULT '{}',
  status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE event_narrative (
  narrative_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id      UUID NOT NULL REFERENCES canonical_event(event_id),
  cluster_id    INTEGER NOT NULL,
  label         TEXT NOT NULL,
  summary       TEXT NOT NULL,
  mention_ids   JSONB NOT NULL DEFAULT '[]',
  model         TEXT NOT NULL,
  unsupported   BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analysis_note (
  note_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  target_type TEXT NOT NULL,
  target_id   TEXT NOT NULL,
  body        TEXT NOT NULL,
  actor       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE review_action (
  action_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  action       TEXT NOT NULL CHECK (action IN ('confirm','exclude','merge','split','annotate')),
  target_type  TEXT NOT NULL,
  target_id    TEXT NOT NULL,
  actor        TEXT NOT NULL,
  reason       TEXT,
  before_state JSONB NOT NULL DEFAULT '{}',
  after_state  JSONB NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mention_label (
  label_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mention_id  UUID NOT NULL REFERENCES event_mention(mention_id),
  label       TEXT NOT NULL CHECK (label IN ('confirm','exclude','uncertain')),
  note        TEXT,
  actor       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE event_assessment (
  event_id               UUID PRIMARY KEY REFERENCES canonical_event(event_id),
  priority               NUMERIC(6,4) NOT NULL DEFAULT 0,
  calibrated_confidence  NUMERIC(6,4) NOT NULL DEFAULT 0,
  abstain                BOOLEAN NOT NULL DEFAULT FALSE,
  burst_score            NUMERIC(10,3) NOT NULL DEFAULT 0,
  components             JSONB NOT NULL DEFAULT '{}',
  promoted               BOOLEAN NOT NULL DEFAULT FALSE,
  computed_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE entity (
  entity_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT NOT NULL UNIQUE,
  description  TEXT NOT NULL DEFAULT '',
  embedding    vector(384),
  watched      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE entity_alias (
  alias_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id  UUID NOT NULL REFERENCES entity(entity_id),
  alias      TEXT NOT NULL,
  UNIQUE (entity_id, alias)
);

CREATE TABLE event_entity (
  event_id     UUID NOT NULL REFERENCES canonical_event(event_id),
  entity_id    UUID NOT NULL REFERENCES entity(entity_id),
  role         TEXT NOT NULL DEFAULT 'mentioned',
  confidence   NUMERIC(4,3) NOT NULL,
  link_method  TEXT NOT NULL CHECK (link_method IN ('rule','semantic','human')),
  PRIMARY KEY (event_id, entity_id)
);

CREATE TABLE entity_review_queue (
  queue_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id    UUID NOT NULL REFERENCES canonical_event(event_id),
  entity_id   UUID NOT NULL REFERENCES entity(entity_id),
  score       NUMERIC(6,4) NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE url_seen (
  normalized_url TEXT PRIMARY KEY,
  mention_id     UUID NOT NULL REFERENCES event_mention(mention_id),
  first_seen     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mention_fingerprint (
  mention_id UUID PRIMARY KEY REFERENCES event_mention(mention_id),
  simhash    TEXT NOT NULL
);

CREATE TABLE timestamp_gap_queue (
  mention_id UUID PRIMARY KEY REFERENCES event_mention(mention_id),
  queued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE probe_run (
  run_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  probe_id          TEXT NOT NULL,
  platform          TEXT NOT NULL,
  backend_id        TEXT NOT NULL,
  acquisition_mode  TEXT,
  success           BOOLEAN NOT NULL,
  item_count        INTEGER NOT NULL DEFAULT 0,
  error             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE probe_failure (
  failure_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  probe_id    TEXT NOT NULL,
  platform    TEXT NOT NULL,
  error       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE probe_health (
  probe_id               TEXT PRIMARY KEY,
  platform               TEXT NOT NULL,
  last_success_at        TIMESTAMPTZ,
  consecutive_failures   INTEGER NOT NULL DEFAULT 0,
  fallback_ratio_24h     NUMERIC(6,4) NOT NULL DEFAULT 0,
  mean_lag_seconds       NUMERIC(12,3),
  status                 TEXT NOT NULL CHECK (status IN ('healthy','degraded','down')),
  detail                 TEXT NOT NULL DEFAULT '',
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingest_log (
  log_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  probe_id    TEXT NOT NULL,
  source_url  TEXT,
  outcome     TEXT NOT NULL,
  detail      TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE alert (
  alert_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id               UUID NOT NULL REFERENCES canonical_event(event_id),
  priority               NUMERIC(6,4) NOT NULL,
  trigger_rules          JSONB NOT NULL,
  calibrated_confidence  NUMERIC(6,4) NOT NULL,
  abstain                BOOLEAN NOT NULL DEFAULT FALSE,
  signal_urls            JSONB NOT NULL,
  summary                TEXT NOT NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (jsonb_array_length(trigger_rules) > 0),
  CHECK (jsonb_array_length(signal_urls) > 0),
  CHECK (abstain = FALSE)
);

CREATE TABLE brief (
  brief_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  period          TEXT NOT NULL CHECK (period IN ('day','week')),
  body            TEXT NOT NULL,
  evidence        JSONB NOT NULL DEFAULT '{}',
  model_assisted  BOOLEAN NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_exchange (
  exchange_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  purpose       TEXT NOT NULL,
  request_hash  TEXT NOT NULL,
  response      JSONB NOT NULL DEFAULT '{}',
  status        TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id          BIGSERIAL PRIMARY KEY,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id   TEXT NOT NULL,
  payload     JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- 標準化 SKU。各平台頁面是同一決策對象的可選方案，不寫入 canonical_event。
CREATE TABLE sku (
  sku_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code        TEXT NOT NULL UNIQUE,
  title       TEXT NOT NULL,
  scene_id    TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 事實：頁面可觀察的標價與優惠原文。求解後的實付不在此表。
CREATE TABLE sku_offer (
  offer_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sku_id                    UUID NOT NULL REFERENCES sku(sku_id),
  platform                  TEXT NOT NULL,
  source_url                TEXT NOT NULL,
  upstream_timestamp        TIMESTAMPTZ,
  collected_at              TIMESTAMPTZ NOT NULL,
  acquisition_mode          TEXT NOT NULL CHECK (acquisition_mode IN ('live','fallback')),
  list_price                NUMERIC(12,2),
  price_basis               TEXT NOT NULL CHECK (price_basis IN ('guest','logged_in')),
  match_confidence          NUMERIC(6,4) NOT NULL,
  page_list_price_dropped   BOOLEAN NOT NULL DEFAULT FALSE,
  protection_cause          TEXT NOT NULL DEFAULT '',
  inventory_reliability     NUMERIC(6,4) NOT NULL,
  after_sales               NUMERIC(6,4) NOT NULL,
  adjustments               JSONB NOT NULL DEFAULT '[]',
  valid_until               TEXT,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE price_observation (
  observation_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sku_id          UUID NOT NULL REFERENCES sku(sku_id),
  observed_on     DATE NOT NULL,
  list_price      NUMERIC(12,2) NOT NULL,
  source_url      TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (sku_id, observed_on, source_url)
);

-- 公開商品圖。只掛 SKU，不寫 canonical_event，也不從圖裡讀價格。
CREATE TABLE product_image (
  image_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sku_id            UUID NOT NULL REFERENCES sku(sku_id),
  source_page_url   TEXT NOT NULL,
  image_url         TEXT NOT NULL,
  content_type      TEXT NOT NULL,
  byte_size         INTEGER NOT NULL,
  sha256            TEXT NOT NULL,
  object_key        TEXT NOT NULL,
  acquisition_mode  TEXT NOT NULL CHECK (acquisition_mode IN ('live','fallback')),
  collected_at      TIMESTAMPTZ NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (sku_id, image_url)
);

-- 出口嘗試。access_denied 表示目標拒絕，系統不得改用其他 IP 再試同一請求。
CREATE TABLE proxy_run (
  run_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  proxy_id     TEXT NOT NULL,
  target_url   TEXT NOT NULL,
  outcome      TEXT NOT NULL CHECK (outcome IN ('success','transport_failure','access_denied')),
  status_code  INTEGER,
  detail       TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 用戶要監測的一種商品。到五個平台查找公開標價，不寫 canonical_event。
CREATE TABLE product_monitor (
  monitor_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scene_id    TEXT NOT NULL,
  query       TEXT NOT NULL,
  sku_code    TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (scene_id, query)
);

CREATE TABLE product_monitor_hit (
  hit_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  monitor_id   UUID NOT NULL REFERENCES product_monitor(monitor_id),
  run_id       UUID NOT NULL,
  platform     TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('priced','withheld','blocked','empty')),
  detail       TEXT NOT NULL,
  source_url   TEXT,
  title        TEXT,
  list_price   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 研判：規則引擎與排序結果。不引用 canonical_event。
CREATE TABLE price_decision (
  decision_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sku_id       UUID NOT NULL REFERENCES sku(sku_id),
  scene_id     TEXT NOT NULL,
  node         TEXT NOT NULL,
  path         TEXT NOT NULL CHECK (path IN ('fast','slow')),
  payload      JSONB NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
