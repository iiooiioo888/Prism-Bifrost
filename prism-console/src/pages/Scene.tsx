import { useEffect, useState } from "react";
import { api, type PricePayload, type ProductImage, type SceneConfig } from "../api";
import { Loading, useAsync } from "../components";

const ORDER_TEXT: Record<string, string> = {
  item: "單品級",
  store: "店鋪級",
  platform: "平台級",
  red_packet: "紅包",
  national_subsidy: "國補",
};

export function ScenePage() {
  const scenes = useAsync(() => api.scenes().then((payload) => payload.items), []);
  const decisions = useAsync(() => api.sceneDecisions("double11").then((payload) => payload.items), []);
  const images = useAsync(() => api.productImages("double11", "DEMO-SKU-01"), []);
  if (scenes.loading || decisions.loading || images.loading) return <Loading />;
  if (scenes.error) return <div className="error">{scenes.error}</div>;
  if (decisions.error) return <div className="error">{decisions.error}</div>;
  if (images.error) return <div className="error">{images.error}</div>;
  const commerce = scenes.data?.find((scene) => scene.scene_id === "double11");
  const harbor = scenes.data?.find((scene) => scene.scene_id === "harbor_watch");
  const latest = decisions.data?.[0]?.payload;
  return (
    <section>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>場景</h2>
      <p style={{ color: "var(--muted)" }}>
        場景掛在同一條流水線上。港務觀測仍歸併成規範事件；雙11的錨點是標準化 SKU，各平台頁面是可選方案，不會被併成同一事件。
      </p>
      <div className="grid two">
        {harbor && <SceneCard scene={harbor} />}
        {commerce && <SceneCard scene={commerce} />}
      </div>
      <ProductImages items={images.data?.items ?? []} />
      {latest ? <Decision payload={latest} /> : <p>尚未產出可複核結論。</p>}
    </section>
  );
}

function SceneCard({ scene }: { scene: SceneConfig }) {
  const dimensions = [
    ["關注對象", scene.dimensions.subjects.join("、")],
    ["訊號域", scene.dimensions.signal_domains.join("、")],
    ["觸發條件", scene.dimensions.triggers.join("、")],
    ["交付終點", scene.dimensions.delivery.join("、")],
    ["時間敏感度", scene.dimensions.time_sensitivity],
  ];
  return (
    <article className="panel">
      <h3 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>{scene.title}</h3>
      <p style={{ color: "var(--muted)" }}>{scene.fusion === "sku_offer" ? "融合方式：標準化 SKU" : "融合方式：規範事件"}</p>
      <div className="kv">
        {dimensions.map(([label, value]) => (
          <span key={label} style={{ display: "contents" }}>
            <span>{label}</span>
            <span>{value}</span>
          </span>
        ))}
      </div>
      {scene.price_rule_order.length > 0 && (
        <p>優惠順序：{scene.price_rule_order.map((layer) => ORDER_TEXT[layer] || layer).join(" → ")}。國補固定最後，順序錯誤會被拒絕。</p>
      )}
    </article>
  );
}

function ProductImages({ items }: { items: ProductImage[] }) {
  return (
    <article className="panel" style={{ marginTop: 22 }}>
      <h3 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>商品圖片</h3>
      <p style={{ color: "var(--muted)" }}>
        只保存允許網域上標成商品圖的公開檔。廣告圖不抓。登入、驗證碼與 robots 禁止的路徑不抓。重新導向不跟隨。目標拒絕時停止，不會換 IP 再試。
      </p>
      {items.length === 0 ? <p>這個 SKU 還沒有公開商品圖。</p> : <Gallery items={items} />}
    </article>
  );
}

function Gallery({ items }: { items: ProductImage[] }) {
  const [urls, setUrls] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    const created: string[] = [];
    Promise.all(items.map((item) => api.productImageBlob(item.image_id)))
      .then((blobs) => {
        if (!alive) return;
        const next = blobs.map((blob) => {
          const url = URL.createObjectURL(blob);
          created.push(url);
          return url;
        });
        setUrls(next);
      })
      .catch(() => {
        if (alive) setUrls([]);
      });
    return () => {
      alive = false;
      created.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [items]);
  return (
    <div className="product-images">
      {items.map((item, index) => (
        <figure key={item.image_id}>
          {urls[index] ? <img src={urls[index]} alt={item.image_url} /> : <div className="empty">圖片讀取中</div>}
          <figcaption>{item.image_url}</figcaption>
        </figure>
      ))}
    </div>
  );
}

function Decision({ payload }: { payload: PricePayload }) {
  const leader = payload.ranking[0];
  const national = leader?.steps.find((step) => step.layer === "national_subsidy");
  return (
    <div style={{ marginTop: 22 }}>
      <h3 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>{payload.sku_title}</h3>
      <p>
        {payload.label}
        {payload.baseline_median
          ? `。歷史價格基線 ${payload.baseline_days} 日，中位數 ${payload.baseline_median}${payload.baseline_median_provisional ? "，尚未滿期，不得當作穩定基線" : ""}`
          : payload.confidence_reduced
            ? `。歷史價格基線 ${payload.baseline_days} 日，未滿 ${payload.baseline_required} 日，結論置信度降低`
            : ""}
        。節點 {payload.node}，路徑 {payload.path === "fast" ? "快速" : "慢速"}。
      </p>
      {payload.sku_match?.conclusion_confidence_cap && (
        <p>結論置信度不得高於 SKU 匹配上限 {payload.sku_match.conclusion_confidence_cap}。</p>
      )}
      <table>
        <thead>
          <tr>
            <th>方案</th><th>標價</th><th>實付</th><th>排序分數</th><th>價保</th><th>匹配上限</th><th>價格基準</th>
          </tr>
        </thead>
        <tbody>
          {payload.ranking.map((offer) => (
            <tr key={offer.offer_id}>
              <td>{offer.platform}</td>
              <td>{offer.list_price}</td>
              <td>{offer.payable}</td>
              <td>{offer.score}</td>
              <td>{offer.price_protection ? "成立" : "不成立"}</td>
              <td>{offer.match_band === "high" ? "高檔" : offer.match_band === "medium" ? "中檔" : "低檔"} {offer.match_cap}</td>
              <td>{offer.price_basis === "guest" ? "遊客態" : offer.price_basis}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {leader && (
        <div className="panel" style={{ marginTop: 14 }}>
          <strong>{leader.platform} 排在前面</strong>
          {leader.price_protection_verdict && <p>{leader.price_protection_verdict.detail}</p>}
          <ul>
            {leader.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          {national && <p>國補計算基數 {national.base_before}，抵扣 {national.deduction}，其後實付 {national.payable_after}。</p>}
        </div>
      )}
      <div className="grid" style={{ marginTop: 14, gridTemplateColumns: "1fr 1fr" }}>
        <Bucket title="可執行結論" lines={payload.abstain.executable.map((item) => item.statement)} />
        <Bucket title="需確認" lines={payload.abstain.confirmations} />
        <Bucket title="風險" lines={payload.abstain.risks} />
        <Bucket title="禁止輸出" lines={payload.abstain.prohibited} />
      </div>
      {payload.withheld.length > 0 && (
        <p style={{ color: "var(--muted)" }}>
          未納入排序：{payload.withheld.map((item) => `${item.platform}（${item.reason}）`).join("；")}
        </p>
      )}
    </div>
  );
}

function Bucket({ title, lines }: { title: string; lines: string[] }) {
  return (
    <article className="panel">
      <h3 style={{ fontFamily: "var(--serif)", fontWeight: 500, fontSize: 18 }}>{title}</h3>
      {lines.length === 0 ? <p style={{ color: "var(--muted)" }}>無</p> : (
        <ul>
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </article>
  );
}
