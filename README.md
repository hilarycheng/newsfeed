# News Feed — Rust feeder

Rust feeder 喺屋企執行，生成單一 `dist/index.html`。
RSS、Steam、CVE 同 HTML 生成都由 Rust 處理，唔需要 Node.js、Python 或 LLM。
CSS、瀏覽器 JavaScript、新聞資料、favicon 同需要嘅授權文字全部內嵌。圖片同 logo 沿用外部 HTTPS 網址。
Rust HTTP 請求共用 Chrome 155／Linux 桌面格式嘅 User-Agent。

## Build

需要 Rust 1.89+（Cargo）。喺專案根目錄執行：

```sh
./build.sh
```

`build.sh` 編譯成功後會將執行檔複製到專案根目錄 `news-feeder`。執行唔需要 Rust toolchain。
Build 過程唔會讀新聞或啟動排程。
`app/index.html`、`app/reader.js`、`app/globals.css` 喺編譯時嵌入執行檔；修改 UI 後需要重新編譯。
不同 OS／CPU 需要各自編譯。

## 執行

喺專案根目錄執行：

```sh
./news-feeder
```

每次執行輸出一行 JSON。成功時 `status` 為 `success`，exit code 為 0。
Console 會即時顯示目前階段、RSS 已完成數／總數，以及每個來源嘅結果。
進度寫入 stderr；stdout 保留一行 JSON，方便 dot 讀取。
完成時會顯示輸出位置，失敗時會顯示階段同原因。程式之後自動結束，返回 terminal。
結果亦寫入 `.sites-runtime/feeder-result.json`。
錯誤日誌位於 `.sites-runtime/feeder.log`。
失敗時保留上一期 `data/feed.json` 同 `dist/`。

沿用一小時內已完成嘅 intake，唔重新讀取來源：

```sh
./news-feeder --from-cache
```

只重新生成現有 `data/feed.json` 嘅 HTML，唔讀來源、唔改快照日期或歷史 state：

```sh
./news-feeder --build-only
```

呢個模式取代原有獨立網站 builder。過期 Steam 優惠會按實際生成時間移除，舊新聞日期保持原樣。

完全關閉 console 輸出（包括 JSON、進度同錯誤訊息）：

```sh
./news-feeder --quiet
./news-feeder --quiet --from-cache
```

Quiet mode 保留 exit code、結果檔案（一般更新亦保留日誌）。成功為 `0`，失敗為 `1`。
可以從 `.sites-runtime/feeder-result.json` 讀取執行結果。

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
3. dot 核對結果內嘅 `sha256` 與 `dist/index.html`。
4. dot 只複製 `dist/index.html` 到目的地。

Cloud computer 接收已生成嘅靜態檔案，唔需要重跑 feeder 或 build。
HTML 可以直接開檔，亦可以放喺 GitHub Pages 嘅發布目錄。
用 branch 發布時，可喺發布目錄加入空白 `.nojekyll` 標記；毋須上傳 CSS／JS 或在 GitHub 執行 Rust。
舊有 `dist/feed.json`、`update-plan.json` 同 `_headers` 已停止輸出；外部複製流程需要改為核對 HTML，hosting headers 由平台管理。
呢個專案唔包含傳送憑證、自動複製、部署或排程設定。

## 本機資料

- 保留 `data/steam-deal-history.json`。佢記錄優惠首次出現同價格變動時間。
- 保留 `data/cve-json-state.json`。佢記錄 checkpoint、版本同已撤銷 CVE。
- `data/` 同 `.sites-runtime/` 只留喺屋企，唔放入公開網站。
- 從舊環境搬過嚟時，先複製現有 `data/`。JSON 格式沿用 Python 版。
- 首次全新執行可以冇 `data/feed.json`。新 Steam history 會以首次觀察建立基準。

唔好用空白 state 覆蓋已有紀錄。

## 離線驗證

```sh
cargo test --locked
```

Rust tests 使用合成 RSS／Atom、Steam fixture、CVE record 同暫存目錄。
包括 cache → feed → 單一 HTML、空白 PATH 執行、資料 escaping、體積上限，以及 build 失敗後保留上一期嘅測試。
Tests 唔會讀取真實來源。`--from-cache` 需要有效 intake，唔係測試指令。

安裝 Chrome／Chromium 後，可執行真實瀏覽器 fixture 測試：

```sh
cargo test --locked browser_reader -- --ignored
```

預設使用 `google-chrome`；其他路徑可透過 `CHROME_BIN` 指定。測試封鎖外部 hostname，核對分類、分頁、無限捲動、手機／桌面欄數、圖片失敗、授權及惡意內容 escaping，唔需要 Node.js 或 Playwright。

HTML 只存一份精簡閱讀資料，卡片 markup 由瀏覽器按頁生成；停用 JavaScript 時顯示首 16 則標題、摘錄同來源。
CSS 移除註解同多餘換行；JavaScript 保留語義需要嘅換行，唔用危險嘅 regex minifier。原出版者文字保持完整。

`scripts/*.py` 保留作移植對照，唔屬於 Rust 執行路徑；舊 Python pipeline 嘅網站生成亦改用 Rust `--build-only`。
原有來源政策見 `SOURCE_POLICY.md`。
