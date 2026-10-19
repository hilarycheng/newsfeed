# News Feed — 離線代碼審閱包

2026-10-18。來源基準 v42，另含未發布的來源錯誤診斷修改。

本包供檢查代碼、備份及執行離線 fixture 測試；沒有自動啟動、部署、排程或安裝程序。沒有放到家用電腦，亦沒有讀取新聞來源或重新發布網站。

## 修改

- 單一來源 HTTP、timeout、XML 等錯誤只略過該來源，其餘來源繼續。
- 加入來源 ID、錯誤分類、HTTP 狀態及成功／失敗／略過／有限來源數。
- 整體失敗附帶 pipeline stage。原有 70%／至少 10 來源及至少 30 卡片門檻不變。
- 新增 tests，證明 188 個正常來源＋1 個失敗來源可以通過門檻，timeout 後正常來源仍可處理。

## 尚未解決的阻礙

最近一次平台存取審核拒絕未提供確切目的地，仍未能安全隔離。現有 URL／hostname／CVE holds 全數保留。這份代碼不授權重新嘗試遭拒請求，也不提供更換機器、路徑或網絡以繞過限制的方式。必須先釐清拒絕目標與範圍，才可重新進行 live fetch。不要執行 run-feeder.py、fetch-news.py 或 fetch-steam-deals.py 來試探。

詳細既有政策見 SOURCE_POLICY.md；它所描述的發布程序不是本離線包的執行指示。

## 離線測試

需要已安裝的 Python 3 及 Node.js；Python tests 只用標準函式庫。從解壓目錄執行：

    python scripts/test-feeder.py
    python scripts/test-steam-lifecycle.py
    python scripts/test-cve-json.py
    python scripts/test-source-isolation.py
    python scripts/test-source-gateway.py
    python scripts/test-cve-transport.py
    python scripts/test-source-holds.py
    python scripts/test-source-health.py
    node --test --test-skip-pattern=Chromium scripts/test-reader.mjs

以上是合成 fixture 測試，非 live feed 測試。Chromium 測試不包括在本包驗證。

## 刻意不包含

Git 歷史、憑證、環境變數檔、Sites hosting 身分、node_modules、日誌、候選新聞、真實文章資料、生成的 HTML、已發布 feed.json、CVE／Steam durable state。這不是可以直接取代現有資料或部署的完整生產快照。不要建立空白 state 取代現有 durable state。

SHA256SUMS.json 列出包內檔案 hash。
