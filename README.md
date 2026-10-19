# News Feed — Rust feeder

Rust feeder 喺屋企執行。dot 將成功生成嘅 `dist/` 複製到 cloud computer。
RSS、Steam、CVE 處理唔需要 Python 或 LLM。網站 build 沿用 Node.js。

## Build

需要 Rust 1.89+（Cargo）同 Node.js 18+。喺專案根目錄執行：

```sh
./build.sh
```

執行檔位於 `target/release/news-feeder`。編譯後執行唔需要 Rust toolchain。
`build.sh` 只做編譯，唔會讀新聞或啟動排程。
不同 OS／CPU 需要各自編譯。

## 執行

喺專案根目錄執行：

```sh
./target/release/news-feeder
```

每次執行輸出一行 JSON。成功時 `status` 為 `success`，exit code 為 0。
結果亦寫入 `.sites-runtime/feeder-result.json`。
錯誤日誌位於 `.sites-runtime/feeder.log`。
失敗時保留上一期 `data/feed.json` 同 `dist/`。

沿用一小時內已完成嘅 intake，唔重新讀取來源：

```sh
./target/release/news-feeder --from-cache
```

## 外部 config

設定喺每次執行時讀取。修改設定後唔需要重新編譯。

| 檔案 | 用途 |
| --- | --- |
| `config/source-registry.json` | RSS 來源、語言、分類、啟用狀態；由原有 `lib/` 搬入 |
| `config/source-endpoints.json` | Steam、iThome metadata、官方 CVE 讀取網址 |
| `config/steam-deals.json` | Steam 遊戲清單 |
| `config/editorial-policy.json` | 篩選同時間窗口 |
| `config/cve-policy.json` | 原有 CVE schema 同開源產品判斷清單 |
| `config/source-holds.json` | 現有 URL／hostname／CVE holds |

現有 holds 同禁止 redirect 嘅行為會保留。移植唔代表解除存取限制。

## dot 複製流程

1. 屋企執行 feeder。
2. dot 確認 `feeder-result.json` 嘅 `status` 為 `success`。
3. dot 核對結果內嘅 `sha256` 與 `dist/feed.json`。
4. dot 將整個 `dist/` 複製到 cloud computer。

Cloud computer 接收已生成嘅靜態檔案，唔需要重跑 feeder 或 build。
呢個專案唔包含傳送憑證、自動複製、部署或排程設定。

## 本機資料

- 保留 `data/steam-deal-history.json`。佢記錄優惠首次出現同價格變動時間。
- 保留 `data/cve-json-state.json`。佢記錄 checkpoint、版本同已撤銷 CVE。
- `data/` 同 `.sites-runtime/` 只留喺屋企，唔放入公開網站。
- 從舊環境搬過嚟時，先複製現有 `data/`。JSON 格式沿用 Python 版。
- 首次全新執行可以冇 `data/feed.json`。新 Steam history 會以首次觀察建立基準。

原有離線包冇包含生產 state，亦冇真實新聞資料。唔好用空白 state 覆蓋已有紀錄。

## 離線驗證

```sh
cargo test --locked
node --test --test-skip-pattern=Chromium scripts/test-reader.mjs
```

Rust tests 使用合成 RSS／Atom、Steam fixture、CVE record 同暫存目錄。
包括 cache → feed → Node build，以及 build 失敗後保留上一期嘅測試。
Tests 唔會讀取真實來源。`--from-cache` 需要有效 intake，唔係測試指令。

`scripts/*.py` 保留作移植對照，唔屬於 Rust 執行路徑。
原有來源政策見 `SOURCE_POLICY.md`。
