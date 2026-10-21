# 稜鏡（Prism Bifröst）

面向公開資訊的持續訊號感知與研判輔助系統。採集、歸併、研判、交付都留在可審計的閉環裡，模型與人工建議在確認之前不改寫事實層。

設計基線見 `docs/PRISM-TD-001.md`。命名、架構、資料表與介面分別在：

- `docs/mythology.md`
- `docs/architecture.md`
- `docs/schema.sql`
- `docs/api.yaml`

## 本地運行

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
prism demo seed
prism serve
```

另一個終端機：

```bash
cd prism-console
npm install
npm run dev
```

工作台開在 `http://127.0.0.1:5173`。介面根路徑 `GET /` 會回服務入口，文件在 `/docs`。開發權杖是檢視者 `dev-viewer`、分析師 `dev-analyst`、管理員 `dev-admin`，只供本地驗收，不要用在公開主機。

## 命令

```bash
prism raven status
prism raven proxies
prism raven images --sku DEMO-SKU-01 --url https://catalog.example/products/demo-sku-01
prism raven probe rss
prism bridge ingest --feed demo-harbor
prism weave event list
prism weave event show <event_id>
prism watch alerts
prism watch score <event_id>
prism herald brief --period day
prism herald review
prism horn test
prism scene list
prism scene platforms
prism scene monitor --query 保溫杯
prism scene listing --url https://item.jd.com/400.html
prism scene decide --sku DEMO-SKU-01 --node onsale
```

`prism horn test` 只做乾跑，不會向外部網址送出。`prism scene decide` 的金額只來自規則引擎，登入態價格不會被採集。`prism raven images` 只抓允許網域上標成商品圖的公開檔，不跟隨重新導向，目標拒絕時不換 IP。`prism scene monitor` 依商品名稱到天貓、淘寶、拼多多、京東、轉轉讀公開標價，寫入 SKU 報價，不建立規範事件。搜尋頁被 robots.txt 禁止或無法確認時，該平台停止，不寫標價。`prism scene listing` 只讀這五個平台的單一商品頁。

## 測試

```bash
python3 -m pytest
```

正式拓撲的 PostgreSQL、Redis 與 MinIO 見 `docker-compose.yml`。第一階段預設用 SQLite 與本地物件目錄，不依賴這三個服務。
