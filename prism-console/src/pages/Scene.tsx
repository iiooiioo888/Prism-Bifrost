import { useEffect, useState } from "react";
import { api, currentRole, type PriceComparison, type PricePayload, type ProductImage, type ProductListing, type ProductMonitorReport, type SceneConfig } from "../api";
import { Loading, useAsync } from "../components";

const AVAILABILITY: Record<string, string> = {
  instock: "有貨",
  outofstock: "缺貨",
  preorder: "預售",
  unknown: "未標示",
};

const ORDER_TEXT: Record<string, string> = {
  item: "單品級",
  store: "店鋪級",
  platform: "平台級",
  red_packet: "紅包",
  national_subsidy: "國補",
};

export function ScenePage() {
  const scenes = useAsync(() => api.scenes().then((payload) => payload.items), []);
  const markets = useAsync(() => api.marketplaces().then((payload) => payload.items), []);
  const decisions = useAsync(() => api.sceneDecisions("double11").then((payload) => payload.items), []);
  const images = useAsync(() => api.productImages("double11", "DEMO-SKU-01"), []);
  const [query, setQuery] = useState("保溫杯");
  const [report, setReport] = useState<ProductMonitorReport | null>(null);
  const [monitorError, setMonitorError] = useState("");
  const [url, setUrl] = useState("https://item.jd.com/400.html");
  const [listing, setListing] = useState<ProductListing | null>(null);
  const [listingError, setListingError] = useState("");
  const [busy, setBusy] = useState(false);
  if (scenes.loading || markets.loading || decisions.loading || images.loading) return <Loading />;
  if (scenes.error) return <div className="error">{scenes.error}</div>;
  if (markets.error) return <div className="error">{markets.error}</div>;
  if (decisions.error) return <div className="error">{decisions.error}</div>;
  if (images.error) return <div className="error">{images.error}</div>;
  const commerce = scenes.data?.find((scene) => scene.scene_id === "double11");
  const latest = decisions.data?.[0]?.payload;
  return (
    <section>
      <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>商品</h2>
      <p style={{ color: "var(--muted)" }}>
        輸入一種商品後，到天貓、淘寶、拼多多、京東、轉轉讀公開標價，並記下頁面寫明的庫存、運費與賣家。同一件商品才對照實付。容量或顏色不同只列單位價格。手動查一次、每天重查，標價變動後再查一次。只有頁面標價下降才告警。
      </p>
      <article className="panel">
        <h3 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>商品平台</h3>
        <p style={{ color: "var(--muted)" }}>
          登入牆、驗證碼與 robots 禁止的路徑不寫價格。重新導向不跟隨。目標拒絕時停止，不會換 IP。頁面上的登入連結不會被當成可以繞過的入口。
        </p>
        <table>
          <thead>
            <tr><th>平台</th><th>搜尋</th><th>商品頁</th></tr>
          </thead>
          <tbody>
            {markets.data?.map((item) => (
              <tr key={item.platform}>
                <td>{item.label}</td>
                <td>{item.search_host}</td>
                <td>{item.hosts.join("、")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {currentRole() !== "viewer" && (
          <form
            style={{ marginTop: 16, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setMonitorError("");
              try {
                setReport(await api.monitorProduct("double11", query));
              } catch (caught) {
                setReport(null);
                setMonitorError(caught instanceof Error ? caught.message : "監測失敗");
              } finally {
                setBusy(false);
              }
            }}
          >
            <label htmlFor="product-query">商品名稱</label>
            <input id="product-query" value={query} onChange={(event) => setQuery(event.target.value)} style={{ minWidth: 240 }} />
            <button className="btn" type="submit" disabled={busy}>{busy ? "監測中" : "在五個平台監測價格"}</button>
          </form>
        )}
        {monitorError && <div className="error" style={{ marginTop: 12 }}>{monitorError}</div>}
        {report && (
          <div style={{ marginTop: 12 }}>
            <p>{report.detail}</p>
            {report.official && <p style={{ color: "var(--muted)" }}>{report.official.detail}</p>}
            {report.alerts?.map((alert) => (
              <p key={alert.source_url} className="error">{alert.detail} {alert.previous_price} → {alert.list_price}</p>
            ))}
            {report.comparison && <Comparison comparison={report.comparison} />}
            {report.recommendations?.flatMap((item) => item.abstain?.executable ?? []).slice(0, 3).map((item) => (
              <p key={item.statement}>{item.statement} 待人工確認。</p>
            ))}
            <table>
              <thead>
                <tr><th>平台</th><th>狀態</th><th>標價</th><th>說明</th></tr>
              </thead>
              <tbody>
                {report.platforms.map((platform) => (
                  <tr key={platform.platform}>
                    <td>{platform.label}</td>
                    <td>{platform.status === "priced" ? "已記錄" : platform.status === "withheld" ? "未寫入標價" : platform.status === "blocked" ? "已停止" : "沒有公開連結"}</td>
                    <td>{platform.items.map((item) => item.list_price).filter(Boolean).join("、") || "—"}</td>
                    <td>
                      {platform.items[0]?.title || platform.detail}
                      {platform.items[0]?.match_band ? `（${platform.items[0].match_band === "high" ? "高檔" : platform.items[0].match_band === "medium" ? "中檔" : "低檔"}）` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {currentRole() !== "viewer" && (
          <form
            style={{ marginTop: 16, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setListingError("");
              try {
                setListing(await api.readListing("double11", url));
              } catch (caught) {
                setListing(null);
                setListingError(caught instanceof Error ? caught.message : "讀取失敗");
              } finally {
                setBusy(false);
              }
            }}
          >
            <label htmlFor="product-url">商品頁</label>
            <input id="product-url" value={url} onChange={(event) => setUrl(event.target.value)} style={{ minWidth: 360 }} />
            <button className="btn" type="submit" disabled={busy}>{busy ? "讀取中" : "讀取商品"}</button>
          </form>
        )}
        {listingError && <div className="error" style={{ marginTop: 12 }}>{listingError}</div>}
        {listing && (
          <p style={{ marginTop: 12 }}>
            {listing.label} {listing.sku_code} {listing.title}{" "}
            {listing.list_price ? `標價 ${listing.list_price}` : "沒有寫入標價"}
            {listing.shipping_price ? `，公開運費 ${listing.shipping_price}` : ""}
            {listing.availability ? `，庫存 ${AVAILABILITY[listing.availability] || listing.availability}` : ""}
            {listing.seller ? `，賣家 ${listing.seller}` : ""}
            。{listing.detail}
          </p>
        )}
      </article>
      <div className="grid two" style={{ marginTop: 22 }}>
        {commerce && <SceneCard scene={commerce} />}
      </div>
      <ProductImages items={images.data?.items ?? []} />
      {latest ? <Decision payload={latest} /> : <p>尚未產出可複核結論。</p>}
    </section>
  );
}

function Comparison({ comparison }: { comparison: PriceComparison }) {
  const groups = comparison.same_identity.filter((group) => group.comparable_count > 0);
  return (
    <div style={{ marginTop: 16 }}>
      <h3 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>實付對照</h3>
      <p style={{ color: "var(--muted)" }}>{comparison.note}</p>
      {groups.length === 0 && <p>這次沒有可對照的同一件商品，標價仍留在下面的表裡。</p>}
      {groups.map((group) => (
        <div key={group.sku_code} style={{ marginTop: 12 }}>
          <strong>{group.title}</strong>
          <p>{group.statement}</p>
          {group.review && <p style={{ color: "var(--warn)" }}>{group.review}</p>}
          <table>
            <thead>
              <tr>
                <th>平台</th><th>標價</th><th>實付</th><th>相差</th><th>運費</th><th>庫存</th><th>單位價格</th><th>說明</th>
              </tr>
            </thead>
            <tbody>
              {group.rows.map((row) => (
                <tr key={row.source_url}>
                  <td>{row.label}{row.seller ? `（${row.seller}）` : ""}</td>
                  <td>{row.list_price}</td>
                  <td>{row.payable || "—"}</td>
                  <td>{row.gap || "—"}</td>
                  <td>{row.shipping_known ? row.shipping : "未公開"}</td>
                  <td>{AVAILABILITY[row.availability] || row.availability}</td>
                  <td>{row.unit_price && row.unit ? `${row.unit_price}/${row.unit}` : "—"}</td>
                  <td>{row.baseline || row.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {comparison.related_specs.map((board) => (
        <div key={`${board.brand}-${board.model}`} style={{ marginTop: 12 }}>
          <strong>{board.brand} {board.model}</strong>
          <p>{board.note}</p>
          <table>
            <thead>
              <tr><th>規格</th><th>平台</th><th>標價</th><th>實付</th><th>單位價格</th><th>匹配</th></tr>
            </thead>
            <tbody>
              {board.rows.map((row) => (
                <tr key={`${row.sku_code}-${row.source_url}`}>
                  <td>{[row.capacity, row.color].filter(Boolean).join(" ") || "—"}</td>
                  <td>{row.label}</td>
                  <td>{row.list_price}</td>
                  <td>{row.payable}</td>
                  <td>{row.unit_price && row.unit ? `${row.unit_price}/${row.unit}` : "—"}</td>
                  <td>{row.match_band === "high" ? "高檔" : row.match_band === "medium" ? "中檔" : "低檔"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
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
            <th>方案</th><th>標價</th><th>實付</th><th>運費</th><th>排序分數</th><th>價保</th><th>匹配上限</th><th>價格基準</th>
          </tr>
        </thead>
        <tbody>
          {payload.ranking.map((offer) => (
            <tr key={offer.offer_id}>
              <td>{offer.platform}</td>
              <td>{offer.list_price}</td>
              <td>{offer.payable}</td>
              <td>{offer.shipping ?? "未公開"}</td>
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
