use crate::{common::*, cve, news, render, sources::App, steam};
use chrono::{Duration, Utc};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    fs::{self, OpenOptions},
    path::Path,
};

pub fn source_counts(statuses: &[Value]) -> Value {
    let count = |status| statuses.iter().filter(|s| s["status"] == status).count();
    json!({"sourceCount":statuses.len(),"sourceSuccessCount":count("ok"),"sourceFailureCount":count("failed"),"sourceSkippedCount":count("skipped"),"sourceLimitedCount":count("limited")})
}

pub fn require_health(statuses: &[Value]) -> Result<Value> {
    let counts = source_counts(statuses);
    let good = counts["sourceSuccessCount"].as_u64().unwrap() as f64;
    if good < 10.0 || good < statuses.len() as f64 * 0.7 {
        return Err("Too many source failures; previous edition preserved".into());
    }
    Ok(counts)
}

fn private_file(path: &Path) -> Result<bool> {
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        if [
            "cve-json-state.json",
            "steam-deal-history.json",
            "_worker.js",
        ]
        .iter()
        .any(|n| entry.file_name() == *n)
            || entry.file_type()?.is_dir() && private_file(&entry.path())?
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn validate(app: &App, feed: &Value, now: Time) -> Result<()> {
    let stories = rows(&feed["stories"]);
    if !(30..=1200).contains(&stories.len()) {
        return Err("Invalid story count".into());
    }
    let (mut ids, mut urls, mut cves) = (HashSet::new(), HashSet::new(), HashSet::new());
    for a in stories {
        let timestamp = if a["kind"] == "deal" {
            s(a, "freshnessAt")
        } else {
            s(a, "publishedAt")
        };
        if s(a, "id").is_empty()
            || s(a, "title").is_empty()
            || s(a, "summary").is_empty()
            || !ids.insert(s(a, "id"))
            || !urls.insert(s(a, "url"))
            || news::medium(app, a)
            || safe_url(s(a, "url"), false).is_none()
            || !current(timestamp, now)
            || format!("{}{}", s(a, "title"), s(a, "summary"))
                .to_lowercase()
                .contains("<script")
        {
            return Err("Invalid or duplicate story".into());
        }
        if a["kind"] == "deal"
            && (!a["publishedAt"].is_null()
                || date(s(a, "firstSeen")).is_none()
                || date(s(a, "checkedAt")).is_none())
        {
            return Err("Invalid Steam observation dates".into());
        }
        for id in rows(&a["cveIds"]) {
            let id = id.as_str().ok_or("Invalid CVE ID")?;
            if !matches(r"^CVE-\d{4}-\d{4,}$", id) || !cves.insert(id.to_uppercase()) {
                return Err("Invalid or duplicate CVE ID".into());
            }
        }
        if rows(&a["relatedArticles"])
            .iter()
            .any(|a| safe_url(s(a, "url"), false).is_none())
        {
            return Err("Invalid related article URL".into());
        }
    }
    if private_file(&app.root.join("dist"))? {
        return Err("Private state found in publication".into());
    }
    Ok(())
}

pub fn run(app: &App, cached: bool) -> Result<Value> {
    run_with(app, cached, render::build)
}

fn run_with(
    app: &App,
    cached: bool,
    build: impl FnOnce(&Path, &Value) -> Result<()>,
) -> Result<Value> {
    let runtime = app.root.join(".sites-runtime");
    fs::create_dir_all(&runtime)?;
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(runtime.join("feeder.lock"))?;
    lock.try_lock()?;
    let target = app.root.join("data/feed.json");
    let old = if target.exists() {
        Some(fs::read(&target)?)
    } else {
        None
    };
    let old_feed: Value = if let Some(bytes) = &old {
        serde_json::from_slice(bytes)?
    } else {
        json!({"stories":[]})
    };
    let backup = tempfile::tempdir_in(&runtime)?;
    let (mut wrote_feed, mut built, mut had_dist) = (false, false, false);
    let mut stage = "preflight";
    let mut health = json!({});
    let mut cve_counts = json!({"status":"failure"});
    let mut log = Vec::new();
    let result = (|| -> Result<Value> {
        if !cached
            && app.holds["networkRefreshHold"]
                .as_str()
                .is_some_and(|s| !s.is_empty())
        {
            return Err("Network refresh held".into());
        }
        stage = "intake";
        if !cached {
            let now = Utc::now();
            news::fetch(app, now)?;
            progress(format_args!("Steam：開始核對價格。"));
            steam::fetch(app, now, rows(&old_feed["stories"]))?;
        } else {
            progress(format_args!("Cache：讀取已完成嘅 RSS／Steam intake。"));
        }
        let now = Utc::now();
        let raw = read_json(&runtime.join("candidates.json"))?;
        recent(s(&raw, "fetchedAt"), now)?;
        if !raw["sourceStatus"].is_array() || !raw["candidates"].is_array() {
            return Err("Invalid RSS intake".into());
        }
        let mut statuses = rows(&raw["sourceStatus"]).to_vec();
        for status in &mut statuses {
            if let Some(source) = rows(&app.registry)
                .iter()
                .find(|s| s["name"] == status["name"])
                && let Some(reason) = app.held_rss(source)
            {
                status["status"] = json!("skipped");
                status["detail"] = json!(reason);
                status["candidateCount"] = json!(0);
            }
            status["fetchedAt"] = raw["fetchedAt"].clone();
            status["cached"] = json!(cached);
        }
        let allowed: HashSet<_> = statuses
            .iter()
            .filter(|s| s["status"] == "ok")
            .map(|row| s(row, "name").to_string())
            .collect();
        stage = "rss_health";
        progress(format_args!("RSS：檢查來源狀態。"));
        health = require_health(&statuses)?;
        for source in &mut statuses {
            if source["name"] == "iThome"
                && source["status"] != "skipped"
                && !rows(&raw["candidates"])
                    .iter()
                    .any(|a| a["source"] == "iThome")
            {
                source["status"] = json!("limited");
                source["detail"] =
                    json!("未能從官方文章頁核實 RSS 日期；只保留仍在 72 小時內的已核實日期項目。");
            }
        }
        stage = "normalize";
        progress(format_args!("篩選：整理及去重新聞。"));
        let mut cards = Vec::new();
        let (mut seen, mut titles) = (HashSet::new(), HashSet::new());
        for candidate in rows(&raw["candidates"])
            .iter()
            .filter(|a| allowed.contains(s(a, "source")))
        {
            let Some(card) = news::normalize(app, candidate, now) else {
                continue;
            };
            let key: String = s(&card, "title")
                .to_lowercase()
                .chars()
                .filter(|c| c.is_alphanumeric() || *c == '_')
                .collect();
            if (seen.contains(s(&card, "url")) || titles.contains(&key))
                && rows(&card["cveIds"]).is_empty()
            {
                continue;
            }
            seen.insert(s(&card, "url").to_string());
            titles.insert(key);
            cards.push(card);
        }
        if !statuses
            .iter()
            .any(|s| s["name"] == "iThome" && s["status"] == "skipped")
        {
            for a in rows(&old_feed["stories"])
                .iter()
                .filter(|a| a["source"] == "iThome" && a["datePrecision"] == "day")
            {
                let mut a = a.clone();
                a["feedExcerpt"] = a["summary"].clone();
                if let Some(mut card) = news::normalize(app, &a, now)
                    && seen.insert(s(&card, "url").to_string())
                {
                    card["summaryType"] = json!("previously_verified_summary");
                    cards.push(card);
                }
            }
        }
        stage = "cve";
        progress(format_args!("CVE：核對來源狀態及整理資料。"));
        let (selected, counts, cve_status) = cve::select(app, cards, now, cached)?;
        let mut cards = selected;
        cve_counts = counts;
        statuses.push(cve_status);
        stage = "steam";
        progress(format_args!("Steam：整理優惠。"));
        let deals = read_json(&runtime.join("steam-deal-candidates.json"))?;
        if !deals.is_array() || rows(&deals).is_empty() {
            return Err("Missing Steam intake observations".into());
        }
        for row in rows(&deals) {
            recent(s(row, "lastAttemptAt"), now)?;
        }
        let steam_failed = rows(&deals).iter().any(|r| r["status"] == "failed");
        let steam_at = rows(&deals)
            .iter()
            .map(|r| s(r, "lastAttemptAt"))
            .min()
            .unwrap();
        statuses.push(json!({"name":"Steam 香港商店","status":if steam_failed { "failed" } else { "ok" },"cached":cached,"fetchedAt":steam_at,
            "detail":if steam_failed { "部分價格核對失敗；保留具時效且明確標示的上次核對資料。" } else { "官方香港商店價格觀察。" }}));
        cards.extend(steam::visible(
            rows(&deals),
            now,
            app.policy["windowHours"].as_i64().unwrap_or(72),
        ));
        stage = "card_health";
        progress(format_args!(
            "篩選：{} 則新聞及優惠，檢查數量及排序。",
            cards.len()
        ));
        if cards.len() < 30 {
            return Err("Fewer than 30 eligible stories; previous edition preserved".into());
        }
        let timestamp =
            |a: &Value| date(a["freshnessAt"].as_str().unwrap_or(s(a, "publishedAt"))).unwrap();
        cards.sort_by_key(|a| std::cmp::Reverse(timestamp(a)));
        cards.truncate(1200);
        let partial = statuses.iter().any(|s| s["status"] != "ok");
        let cache_note = if cached {
            format!(
                "本期沿用已完成的來源讀取（RSS {}）；並未重新讀取全部來源。 ",
                s(&raw, "fetchedAt")
            )
        } else {
            String::new()
        };
        let feed = json!({"updatedAt":stamp(now),"editionDate":(now+Duration::hours(8)).format("%Y-%m-%d").to_string(),"stories":cards,"sourceStatus":statuses,
            "intake":{"cached":cached,"rssFetchedAt":raw["fetchedAt"],"steamFetchedAt":steam_at,"partial":partial},
            "note":format!("{cache_note}自動 RSS 整理：標題與短摘錄取自來源，未經 AI 改寫。規則篩選可能有遺漏；討論時間不代表原文日期。"),"editorialStats":{"medium":0,"total":cards.len(),"modelCalls":0}});
        stage = "build";
        progress(format_args!("Build：生成靜態網站。"));
        let published = render::prepare(&feed, now)?;
        write_json(&target, &feed)?;
        wrote_feed = true;
        if app.root.join("dist").exists() {
            fs::rename(app.root.join("dist"), backup.path().join("dist"))?;
            had_dist = true;
        }
        built = true;
        build(&app.root, &published)?;
        stage = "validation";
        progress(format_args!("驗證：檢查 feed 及輸出檔案。"));

        validate(app, &published, now)?;
        let good = statuses.iter().filter(|s| s["status"] == "ok").count();
        Ok(
            json!({"status":"success","rssHealth":health,"partial":partial,"cached":cached,"rssFetchedAt":raw["fetchedAt"],"itemCount":rows(&published["stories"]).len(),
            "sourceCount":statuses.len(),"failureCount":statuses.len()-good,"failedSources":statuses.iter().filter(|row| ["failed","skipped"].contains(&s(row,"status"))).take(8).map(|s| s["name"].clone()).collect::<Vec<_>>(),
            "limitedSourceCount":statuses.iter().filter(|s| s["status"] == "limited").count(),"dealCount":cards.iter().filter(|c| c["kind"] == "deal").count(),
            "cveCount":cards.iter().filter(|c| !rows(&c["cveIds"]).is_empty()).count(),"cveJSON":cve_counts,"modelCalls":0,"artifact":"dist/index.html","sha256":hash(&fs::read(app.root.join("dist/index.html"))?)}),
        )
    })();
    let result = match result {
        Ok(result) => result,
        Err(e) => {
            let rollback = (|| -> Result<()> {
                if wrote_feed {
                    if let Some(old) = old {
                        write_bytes(&target, &old)?;
                    } else {
                        fs::remove_file(&target)?;
                    }
                }
                if built {
                    if app.root.join("dist").exists() {
                        fs::remove_dir_all(app.root.join("dist"))?;
                    }
                    if had_dist {
                        fs::rename(backup.path().join("dist"), app.root.join("dist"))?;
                    }
                }
                Ok(())
            })();
            if let Err(error) = &rollback {
                log.push(format!("Rollback failed: {error}"));
                let saved = backup.keep();
                log.push(format!("Previous output retained at {}", saved.display()));
            }
            json!({"status":"failure","stage":stage,"error":cut(&e.to_string(),180),"rssHealth":health,"preservedPreviousEdition":rollback.is_ok(),"modelCalls":0,"cveJSON":cve_counts})
        }
    };
    write_bytes(&runtime.join("feeder.log"), log.join("\n").as_bytes())?;
    write_json(&runtime.join("feeder-result.json"), &result)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn isolated_source_failure_and_threshold() {
        let mut statuses = vec![json!({"status":"ok"}); 188];
        statuses.push(json!({"status":"failed"}));
        assert_eq!(require_health(&statuses).unwrap()["sourceFailureCount"], 1);
        assert!(require_health(&vec![json!({"status":"ok"}); 9]).is_err());
    }

    #[test]
    fn cached_build_and_failed_build_preserve_previous_edition() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path();
        let source = Path::new(env!("CARGO_MANIFEST_DIR"));
        for dir in ["config", "data", ".sites-runtime"] {
            fs::create_dir(root.join(dir)).unwrap();
        }
        for name in [
            "source-registry.json",
            "source-holds.json",
            "editorial-policy.json",
            "steam-deals.json",
            "source-endpoints.json",
            "cve-policy.json",
        ] {
            fs::copy(
                source.join("config").join(name),
                root.join("config").join(name),
            )
            .unwrap();
        }
        let now = Utc::now();
        let mut candidates: Vec<_> = (0..30).map(|i| json!({"source":"Inside Java","title":format!("Java fixture {i}"),"feedExcerpt":"Synthetic fixture only.","url":format!("https://example.com/{i}"),"publishedAt":stamp(now)})).collect();
        candidates[0]["source"] = json!("Elixir 官方部落格");
        candidates[0]["title"] = json!("Elixir fixture");
        let mut statuses = vec![json!({"name":"Inside Java","status":"ok"})];
        statuses.extend((1..10).map(|i| json!({"name":format!("Fixture {i}"),"status":"ok"})));
        statuses.push(json!({"name":"Elixir 官方部落格","status":"ok"}));
        write_json(
            &root.join(".sites-runtime/candidates.json"),
            &json!({"fetchedAt":stamp(now),"candidates":candidates,"sourceStatus":statuses}),
        )
        .unwrap();
        write_json(
            &root.join(".sites-runtime/steam-deal-candidates.json"),
            &json!([{"status":"not_on_sale","lastAttemptAt":stamp(now)}]),
        )
        .unwrap();
        let app = App::load(root.into()).unwrap();
        let result = run(&app, true).unwrap();
        assert_eq!(result["status"], "success", "{result}");
        assert_eq!(result["itemCount"], 30);
        assert!(root.join("dist/index.html").exists());
        assert_eq!(fs::read_dir(root.join("dist")).unwrap().count(), 1);
        assert!(
            fs::read_to_string(root.join("dist/index.html"))
                .unwrap()
                .contains("Apache-2.0 授權全文")
        );
        let old_feed = fs::read(root.join("data/feed.json")).unwrap();
        let old_html = fs::read(root.join("dist/index.html")).unwrap();
        let failed = run_with(&app, true, |root, _| {
            write_bytes(&root.join("dist/index.html"), b"partial build")?;
            Err("Simulated output failure".into())
        })
        .unwrap();
        assert_eq!(failed["status"], "failure");
        assert_eq!(failed["stage"], "build");
        assert_eq!(fs::read(root.join("data/feed.json")).unwrap(), old_feed);
        assert_eq!(fs::read(root.join("dist/index.html")).unwrap(), old_html);
    }
}
