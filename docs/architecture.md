# 架構與部署拓撲

五層流水線保持單向依賴：探針不知道歸併，模型不知道事實表，指標表只有 `prism/weave/metrics.py` 的 `persist_ready` 能寫。

```
探針 raven → 原始條目 → 採集 bridge → 提及
                                      ↓
                              融合 weave（三檔）
                                      ↓
                    高置信寫事實層｜中置信只寫歸併建議
                                      ↓
                              研判 watch → 告警 horn
                                      ↓
                              簡報與工作台 herald / console
```

第一階段程式以單一 Python 行程執行這條管道，任務介面是同步呼叫。Celery 與 Redis 留在部署拓撲裡，預設不啟動。主資料庫契約以 `docs/schema.sql` 為準，開發與測試使用 SQLite，正式環境使用 PostgreSQL 16 與 pgvector。

部署最小集：

| 組件 | 第一階段實作 | 正式拓撲 |
|---|---|---|
| 核心服務 | FastAPI，`prism serve` | 兩個以上副本 |
| 工作節點 | 行程內執行 | Celery／RQ |
| 主資料庫 | SQLite 或 PostgreSQL | PostgreSQL 主從 |
| 物件存儲 | 本地目錄 `var/objects` | MinIO |
| 佇列 | 無 | Redis 7 |
| 控制台 | `prism-console` | 靜態資源與反向代理 |

開發用權杖寫在 `config/prism.yaml`，只供本地驗收。正式環境改由密鑰管理注入，不沿用檔案裡的值。

場景掛在這條流水線上，不另開管道。`config/scenes.yaml` 用融合方式區分規範事件與標準化 SKU。SKU 的報價進 `sku_offer`，實付與排序進 `price_decision`，兩者都不寫 `canonical_event`。優惠順序、模型禁產金額與棄權分級都在程式結構裡，不靠提示詞。

雙11的規則引擎在 `prism/commerce/`，不在 `prism/weave/`。事件指標仍只有 `persist_ready` 能寫。價格基線中位數由 `prism/commerce/baseline.py` 計算，不寫入 `event_metrics`。`RawItem` 上的 SKU 與價格欄位是可選介面，採集與歸併忽略它們。
