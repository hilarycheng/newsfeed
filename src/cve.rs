use crate::{
    common::*,
    news::{EXCLUDED, MAJOR, OTHER_LANGUAGE},
    sources::App,
};
use chrono::{Duration, NaiveDateTime};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    time::Instant,
};

const SOURCE: &str = "CVE List V5";

fn valid_id(id: &str) -> bool {
    matches(r"^CVE-\d{4}-\d{4,19}$", id)
}

fn cve_date(value: &str) -> Result<Time> {
    date(value)
        .or_else(|| {
            NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%.f")
                .ok()
                .map(|d| d.and_utc())
        })
        .ok_or_else(|| "missing_or_invalid_cve_date".into())
}

pub fn empty_state() -> Value {
    json!({"version":1,"checkpoint":null,"records":{},"versions":{},"tombstones":{},"quarantine":{},"failures":{},"pending":{}})
}

pub fn read_state(app: &App) -> Result<Value> {
    let path = app.root.join("data/cve-json-state.json");
    if !path.exists() {
        return Ok(empty_state());
    }
    let state = read_json(&path)?;
    if state["version"] != 1
        || [
            "records",
            "versions",
            "tombstones",
            "quarantine",
            "failures",
            "pending",
        ]
        .iter()
        .any(|key| !state[key].is_object())
    {
        return Err("Invalid CVE state; preserve and repair it".into());
    }
    Ok(state)
}

fn oss(app: &App, affected: &[Value]) -> bool {
    affected.iter().any(|a| {
        let vendor = s(a, "vendor").trim().to_lowercase();
        let product = s(a, "product").trim().to_lowercase();
        if rows(&app.cve_policy["products"][&vendor])
            .iter()
            .any(|p| p == &product)
        {
            return true;
        }
        if ["apache software foundation", "apache"].contains(&vendor.as_str())
            && rows(&app.cve_policy["products"]["apache"])
                .iter()
                .any(|p| p == product.strip_prefix("apache ").unwrap_or(&product))
        {
            return true;
        }
        let package = s(a, "packageURL")
            .split(['@', '?', '#'])
            .next()
            .unwrap_or("")
            .to_lowercase();
        if rows(&app.cve_policy["packages"])
            .iter()
            .any(|p| p == &package)
        {
            return true;
        }
        if let Ok(repo) = reqwest::Url::parse(s(a, "repo"))
            && repo.scheme() == "https"
            && repo.host_str() == Some("github.com")
        {
            let path = repo
                .path()
                .trim_matches('/')
                .trim_end_matches(".git")
                .to_lowercase();
            return rows(&app.cve_policy["repositories"])
                .iter()
                .any(|p| p == &path);
        }
        false
    })
}

pub fn projection(app: &App, record: &Value, expected: &str) -> Result<Value> {
    if record["dataType"] != "CVE_RECORD" {
        return Err("unknown_record_type".into());
    }
    if !rows(&app.cve_policy["dataVersions"]).contains(&record["dataVersion"]) {
        return Err("unsupported_schema".into());
    }
    let meta = &record["cveMetadata"];
    if meta["cveId"] != expected || !valid_id(expected) {
        return Err("record_id_mismatch".into());
    }
    if meta["state"] == "REJECTED" {
        return Ok(json!({"state":"REJECTED","card":null}));
    }
    if meta["state"] != "PUBLISHED" {
        return Err("unknown_record_state".into());
    }
    let published = stamp(cve_date(s(meta, "datePublished"))?);
    let cna = &record["containers"]["cna"];
    if !cna.is_object() || !cna["affected"].is_array() {
        return Err("invalid_cna".into());
    }
    let containers: Vec<_> = std::iter::once(cna)
        .chain(
            rows(&record["containers"]["adp"])
                .iter()
                .filter(|a| a.is_object()),
        )
        .collect();
    let description = rows(&cna["descriptions"])
        .iter()
        .find(|d| s(d, "lang").to_lowercase().starts_with("en"))
        .map(|d| plain(s(d, "value"), 350))
        .unwrap_or_default();
    let title = plain(s(cna, "title"), 180);
    let title = if title.is_empty() {
        expected.into()
    } else {
        title
    };
    let text = format!("{title} {description}");
    let exclusion = format!(
        "{text} {}",
        rows(&cna["affected"])
            .iter()
            .map(|a| format!("{} {}", s(a, "vendor"), s(a, "product")))
            .collect::<Vec<_>>()
            .join(" ")
    );
    let mut ratings = Vec::new();
    for (i, container) in containers.iter().enumerate() {
        let provider = container["providerMetadata"]["shortName"]
            .as_str()
            .or(container["providerMetadata"]["orgId"].as_str())
            .unwrap_or(if i == 0 { "CNA" } else { "ADP" });
        for metric in rows(&container["metrics"]) {
            for (key, version) in [
                ("cvssV4_0", "4.0"),
                ("cvssV3_1", "3.1"),
                ("cvssV3_0", "3.0"),
                ("cvssV2_0", "2.0"),
            ] {
                let value = &metric[key];
                if let Some(score) = value["baseScore"]
                    .as_f64()
                    .filter(|s| (0.0..=10.0).contains(s))
                {
                    ratings.push(json!({"version":version,"score":score,"source":plain(provider,120),"vector":plain(s(value,"vectorString"),200)}));
                }
            }
        }
    }
    let mut result = json!({"state":"PUBLISHED","publishedAt":published,"card":null,"dataVersion":record["dataVersion"]});
    if matches(OTHER_LANGUAGE, &text)
        || matches(EXCLUDED, &exclusion)
        || !oss(app, rows(&cna["affected"]))
        || !(matches(MAJOR, &text)
            || ratings
                .iter()
                .any(|r| r["score"].as_f64().unwrap_or(0.0) >= 9.0))
    {
        return Ok(result);
    }
    let mut refs = Vec::new();
    for container in containers {
        for r in rows(&container["references"]) {
            if safe_url(s(r, "url"), false).is_some() && !refs.contains(&r["url"]) {
                refs.push(r["url"].clone());
            }
        }
    }
    let rating = if ratings.is_empty() {
        "未評分".into()
    } else {
        ratings
            .iter()
            .map(|r| {
                format!(
                    "CVSS {} {} ({})",
                    s(r, "version"),
                    r["score"].as_f64().unwrap(),
                    s(r, "source")
                )
            })
            .collect::<Vec<_>>()
            .join(" · ")
    };
    result["card"] = json!({"id":expected,"cveIds":[expected],"category":"資安","title":if title.contains(expected) { title.clone() } else { format!("{expected} · {title}") },
        "summary":if description.is_empty() { &title } else { &description },"summaryType":if description.is_empty() { "title_only" } else { "official_cve_description" },
        "source":SOURCE,"url":format!("https://www.cve.org/CVERecord?id={expected}"),"publishedAt":published,"datePrecision":"time","sourceLanguage":"en",
        "cveRating":rating,"cvss":ratings,"cveReferences":refs,"relatedArticles":[]});
    Ok(result)
}

pub fn discover(log: &Value, state: &Value, now: Time) -> Result<(Value, String)> {
    if !log.is_array() || rows(log).is_empty() {
        return Err("invalid_delta_log".into());
    }
    let mut events = Vec::new();
    for item in rows(log) {
        let when = cve_date(s(item, "fetchTime"))?;
        if when > now + Duration::minutes(5) {
            return Err("future_delta_event".into());
        }
        events.push((when, item));
    }
    events.sort_by_key(|(d, _)| *d);
    let checkpoint = if state["checkpoint"].is_string() {
        cve_date(s(state, "checkpoint"))?
    } else {
        now - Duration::hours(72)
    };
    if events[0].0 > checkpoint {
        return Err("delta_history_gap".into());
    }
    if events.last().unwrap().0 < checkpoint {
        return Err("stale_delta_log".into());
    }
    let mut pending = state["pending"].clone();
    for (_, event) in events.iter().filter(|(d, _)| *d > checkpoint) {
        if !event["error"].is_null()
            && event["error"] != false
            && event["error"] != ""
            && event["error"] != json!([])
        {
            return Err("upstream_delta_error".into());
        }
        for group in ["new", "updated"] {
            if !event[group].is_array() {
                return Err("invalid_delta_group".into());
            }
            for item in rows(&event[group]) {
                let id = s(item, "cveId");
                if !valid_id(id) {
                    return Err("invalid_delta_id".into());
                }
                let version = cve_date(s(item, "dateUpdated"))?;
                if pending[id].is_null() || cve_date(s(&pending, id))? < version {
                    pending[id] = json!(stamp(version));
                }
            }
        }
    }
    pending
        .as_object_mut()
        .ok_or("invalid_pending_state")?
        .retain(|id, v| state["versions"][id] != *v);
    Ok((pending, stamp(events.last().unwrap().0)))
}

pub fn record_url(base: &str, id: &str) -> Result<String> {
    if !valid_id(id) {
        return Err("invalid_cve_id".into());
    }
    let parts: Vec<_> = id.split('-').collect();
    Ok(format!(
        "{}{}/{}xxx/{id}.json",
        base,
        parts[1],
        &parts[2][..parts[2].len() - 3]
    ))
}

pub fn refresh(
    app: &App,
    now: Time,
    extra: &[String],
    fetch: &(impl Fn(&str, u64) -> Result<Value> + Sync),
) -> Result<Value> {
    let mut state = read_state(app)?;
    let start = Instant::now();
    let mut counts =
        json!({"status":"failure","success":0,"skips":0,"errors":0,"pending":0,"quarantined":0});
    let result = (|| -> Result<()> {
        let revision = fetch(s(&app.endpoints, "cveRevision"), 100_000)?;
        let revision = s(&revision["object"], "sha");
        if !matches("^[0-9a-f]{40}$", revision) {
            return Err("invalid_repository_revision".into());
        }
        let snapshot = s(&app.endpoints, "cveBase").replace("/main/", &format!("/{revision}/"));
        let log = fetch(&format!("{snapshot}deltaLog.json"), 40_000_000)?;
        state["repositoryRevision"] = json!(revision);
        let (mut pending, target) = discover(&log, &state, now)?;
        for id in extra {
            if !valid_id(id) {
                return Err("invalid_extra_id".into());
            }
            if state["versions"][id].is_null() && pending[id].is_null() {
                pending[id] = json!("1970-01-01T00:00:00Z");
            }
        }
        state["pending"] = pending;
        state["targetCheckpoint"] = json!(target);
        counts["discovered"] = json!(state["pending"].as_object().unwrap().len());
        let mut jobs: Vec<_> = state["pending"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(id, v)| (id.clone(), v.as_str().unwrap_or("").to_string()))
            .collect();
        jobs.sort_by(|a, b| (&a.1, &a.0).cmp(&(&b.1, &b.0)));
        jobs.truncate(2000);
        let results = parallel(&jobs, 12, |(id, version)| {
            let result = (|| -> Result<Value> {
                if start.elapsed().as_secs() >= 240 {
                    return Err("run_time_limit".into());
                }
                let raw = fetch(&record_url(&snapshot, id)?, 2_000_000)?;
                if start.elapsed().as_secs() >= 240 {
                    return Err("run_time_limit".into());
                }
                if raw["cveMetadata"]["dateUpdated"].is_string()
                    && cve_date(s(&raw["cveMetadata"], "dateUpdated"))? < cve_date(version)?
                {
                    return Err("stale_record_version".into());
                }
                projection(app, &raw, id)
            })();
            (id.clone(), version.clone(), result)
        });
        for (id, version, result) in results {
            match result {
                Err(e) => {
                    counts["errors"] = json!(counts["errors"].as_u64().unwrap() + 1);
                    state["failures"][&id] =
                        json!({"at":stamp(now),"error":cut(&e.to_string(),120)});
                    if !e.is::<crate::sources::SourceError>() {
                        state["quarantine"][&id] = json!({"at":stamp(now),"reason":cut(&e.to_string(),120),"version":version});
                        counts["quarantined"] = json!(counts["quarantined"].as_u64().unwrap() + 1);
                    }
                }
                Ok(record) => {
                    counts["success"] = json!(counts["success"].as_u64().unwrap() + 1);
                    if record["state"] == "REJECTED" {
                        state["tombstones"][&id] = json!({"at":stamp(now),"version":version});
                        state["records"].as_object_mut().unwrap().remove(&id);
                    } else if state["tombstones"][&id].is_null() {
                        if record["card"].is_null() {
                            counts["skips"] = json!(counts["skips"].as_u64().unwrap() + 1);
                        }
                        state["records"][&id] = record;
                    }
                    state["versions"][&id] = json!(version);
                    for field in ["pending", "failures", "quarantine"] {
                        state[field].as_object_mut().unwrap().remove(&id);
                    }
                }
            }
        }
        if state["pending"].as_object().unwrap().is_empty() {
            state["checkpoint"] = json!(target);
            counts["status"] = json!("success");
            state.as_object_mut().unwrap().remove("lastError");
        } else {
            state["lastError"] = json!("incomplete_delta_batch");
        }
        state["records"].as_object_mut().unwrap().retain(|_, r| {
            date(s(r, "publishedAt")).is_some_and(|d| d >= now - Duration::hours(72))
        });
        Ok(())
    })();
    if let Err(e) = result {
        counts["errors"] = json!(counts["errors"].as_u64().unwrap() + 1);
        state["lastError"] = json!(cut(&e.to_string(), 120));
    }
    counts["pending"] = json!(state["pending"].as_object().unwrap().len());
    state["lastAttemptAt"] = json!(stamp(now));
    state["lastResult"] = counts.clone();
    write_json(&app.root.join("data/cve-json-state.json"), &state)?;
    Ok(counts)
}

pub fn merge(cards: Vec<Value>, state: &Value, now: Time) -> Vec<Value> {
    let mut canonical = BTreeMap::new();
    if let Some(records) = state["records"].as_object() {
        for (id, record) in records {
            let mut card = record["card"].clone();
            if card.is_object()
                && state["tombstones"][id].is_null()
                && current(s(&card, "publishedAt"), now)
            {
                card["relatedArticles"] = json!([]);
                canonical.insert(id.clone(), card);
            }
        }
    }
    let mut ordinary = Vec::new();
    for card in cards {
        let ids: BTreeSet<_> = rows(&card["cveIds"])
            .iter()
            .filter_map(Value::as_str)
            .filter(|id| valid_id(id))
            .collect();
        if ids.is_empty() {
            ordinary.push(card);
            continue;
        }
        for id in ids {
            let Some(canonical) = canonical.get_mut(id) else {
                continue;
            };
            let Some(url) = safe_url(s(&card, "url"), false) else {
                continue;
            };
            let links = canonical["relatedArticles"].as_array_mut().unwrap();
            if !links.iter().any(|l| l["url"] == url) {
                links.push(json!({"url":url,"title":plain(s(&card,"title"),180),"source":plain(s(&card,"source"),120)}));
            }
        }
    }
    ordinary.extend(canonical.into_values());
    ordinary
}

pub fn select(
    app: &App,
    cards: Vec<Value>,
    now: Time,
    cached: bool,
) -> Result<(Vec<Value>, Value, Value)> {
    let mut state = read_state(app)?;
    let held = app.holds["cve"].as_str().filter(|s| !s.is_empty());
    let mut counts = state["lastResult"]
        .as_object()
        .map(|m| Value::Object(m.clone()))
        .unwrap_or_else(|| json!({"status":"failure"}));
    if held.is_some() {
        counts["status"] = json!("skipped");
        counts["pending"] = json!(state["pending"].as_object().unwrap().len());
        counts["cached"] = json!(cached);
    } else if cached {
        counts["cached"] = json!(true);
    } else {
        let extra: Vec<_> = cards
            .iter()
            .flat_map(|a| rows(&a["cveIds"]))
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        counts = refresh(app, now, &extra, &|url, limit| {
            app.get_json("cve", SOURCE, url, limit)
        })?;
        state = read_state(app)?;
    }
    if counts["status"] != "success" {
        let status = json!({"name":SOURCE,"status":if held.is_some() { "skipped" } else { "failed" },"detail":held.unwrap_or("官方 CVE JSON 讀取未完成；本期略過 CVE，保留待處理進度。"),
            "pending":state["pending"].as_object().unwrap().len(),"cached":cached,"lastAttemptAt":state["lastAttemptAt"]});
        return Ok((
            cards
                .into_iter()
                .filter(|a| rows(&a["cveIds"]).is_empty())
                .collect(),
            counts,
            status,
        ));
    }
    Ok((
        merge(cards, &state, now),
        counts,
        json!({"name":SOURCE,"status":"ok","cached":cached,"detail":"官方 CVE JSON；依首次公布日期與既有重大開源漏洞規則篩選。"}),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn refresh_persists_checkpoint_and_retries_pending_records() {
        let mut app = App::load(env!("CARGO_MANIFEST_DIR").into()).unwrap();
        let temp = tempfile::tempdir().unwrap();
        app.root = temp.path().into();
        let now = date("2026-10-19T00:00:00Z").unwrap();
        let id = "CVE-2026-12345";
        let broken = std::sync::atomic::AtomicBool::new(true);
        let fetch = |url: &str, _: u64| -> Result<Value> {
            if url == s(&app.endpoints, "cveRevision") {
                return Ok(json!({"object":{"sha":"a".repeat(40)}}));
            }
            if url.ends_with("deltaLog.json") {
                return Ok(json!([
                    {"fetchTime":stamp(now),"new":[{"cveId":id,"dateUpdated":stamp(now)}],"updated":[],"error":[]},
                    {"fetchTime":stamp(now-Duration::days(4)),"new":[],"updated":[],"error":[]}
                ]));
            }
            if broken.load(std::sync::atomic::Ordering::Relaxed) {
                return Err(crate::sources::source_error("transport_error"));
            }
            Ok(
                json!({"dataType":"CVE_RECORD","dataVersion":"5.2.0","cveMetadata":{"cveId":id,"state":"REJECTED","dateUpdated":stamp(now)}}),
            )
        };
        assert_eq!(
            refresh(&app, now, &[], &fetch).unwrap()["status"],
            "failure"
        );
        let state = read_state(&app).unwrap();
        assert!(state["checkpoint"].is_null());
        assert_eq!(state["pending"][id], stamp(now));
        broken.store(false, std::sync::atomic::Ordering::Relaxed);
        assert_eq!(
            refresh(&app, now, &[], &fetch).unwrap()["status"],
            "success"
        );
        let state = read_state(&app).unwrap();
        assert_eq!(state["checkpoint"], stamp(now));
        assert!(state["tombstones"][id].is_object());
        assert_eq!(refresh(&app, now, &[], &fetch).unwrap()["success"], 0);
    }

    #[test]
    fn projection_discovery_and_rejection() {
        let app = App::load(env!("CARGO_MANIFEST_DIR").into()).unwrap();
        let now = date("2026-10-19T00:00:00Z").unwrap();
        let id = "CVE-2026-12345";
        let mut record = json!({"dataType":"CVE_RECORD","dataVersion":"5.2.0","cveMetadata":{"cveId":id,"state":"PUBLISHED","datePublished":stamp(now)},
            "containers":{"cna":{"title":"Fixture remote code execution","descriptions":[{"lang":"en","value":"Synthetic fixture only."}],"affected":[{"vendor":"Apache Software Foundation","product":"Apache Tomcat"}]}}});
        let mut state = empty_state();
        state["records"][id] = projection(&app, &record, id).unwrap();
        assert_eq!(merge(vec![], &state, now).len(), 1);
        assert!(merge(vec![], &state, now + Duration::hours(73)).is_empty());
        state["tombstones"][id] = json!({"at":stamp(now)});
        assert!(merge(vec![], &state, now).is_empty());
        record["cveMetadata"]["state"] = json!("REJECTED");
        assert!(projection(&app, &record, id).unwrap()["card"].is_null());
        let log = json!([{"fetchTime":stamp(now),"new":[{"cveId":id,"dateUpdated":stamp(now)}],"updated":[],"error":[]},
            {"fetchTime":stamp(now-Duration::days(4)),"new":[],"updated":[],"error":[]}]);
        let (pending, _) = discover(&log, &state, now).unwrap();
        assert_eq!(pending[id], stamp(now));
        state["versions"][id] = json!(stamp(now));
        assert!(
            discover(&log, &state, now)
                .unwrap()
                .0
                .as_object()
                .unwrap()
                .is_empty()
        );
        assert!(record_url("https://example.com/", "../../bad").is_err());
    }
}
