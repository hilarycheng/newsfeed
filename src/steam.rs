use crate::{
    common::*,
    sources::{App, steam_url},
};
use chrono::Duration;
use serde_json::{Value, json};
use std::{collections::HashSet, fs::OpenOptions, path::Path};

const MIGRATION: &str = "Unknown firstSeen uses the migration baseline time, not a historical sale start. Existing checkedAt is retained only as price-check evidence. Unchanged checks never reset the 72-hour news window.";

fn identity(row: &Value) -> Result<(String, String, String)> {
    let app = s(row, "appId").parse::<u64>()?;
    let region = row["storeRegion"].as_str().unwrap_or("HK").to_uppercase();
    let currency = row["currency"].as_str().unwrap_or("HKD").to_uppercase();
    if app == 0 || !matches("^[A-Z]{2}$", &region) || !matches("^[A-Z]{3}$", &currency) {
        return Err("Invalid Steam app/region/currency identity".into());
    }
    Ok((app.to_string(), region, currency))
}

fn key(row: &Value) -> Result<String> {
    let (app, region, currency) = identity(row)?;
    Ok(format!("{app}:{region}:{currency}"))
}

fn price(row: &Value) -> Option<Value> {
    let p = &row["price"];
    let (final_price, initial, discount) = (
        p["finalMinor"].as_i64()?,
        p["initialMinor"].as_i64()?,
        p["discountPercent"].as_i64()?,
    );
    if !(0 <= final_price && final_price <= initial && (0..=100).contains(&discount)) {
        return None;
    }
    Some(json!({"finalMinor":final_price,"initialMinor":initial,"discountPercent":discount}))
}

fn entry(row: &Value) -> Result<Value> {
    let (app, region, currency) = identity(row)?;
    Ok(
        json!({"appId":app,"storeRegion":region,"currency":currency,"firstSeen":null,"firstSeenBasis":null,"lastSeen":null,
        "priceChangedAt":null,"checkedAt":null,"lastAttemptAt":null,"lastCheckStatus":null,"active":false,"inactiveSince":null,"price":null,"snapshot":null}),
    )
}

pub fn new_history(now: Time, legacy: &[Value]) -> Value {
    let when = stamp(now);
    let mut history = json!({"schemaVersion":1,"initializedAt":when,"migration":{"baselineAt":when,"policy":MIGRATION},"offers":{}});
    let app_pattern = regex::Regex::new(r"/app/(\d+)(/|$)").unwrap();
    for card in legacy.iter().filter(|c| c["kind"] == "deal") {
        let mut row = card["deal"].clone();
        if !row.is_object() {
            row = json!({});
        }
        if row["appId"].is_null() {
            row["appId"] = card["appId"].clone();
            if row["appId"].is_null()
                && let Some(c) = app_pattern.captures(s(card, "url"))
            {
                row["appId"] = json!(&c[1]);
            }
        }
        if let Some(n) = row["appId"].as_u64() {
            row["appId"] = json!(n.to_string());
        }
        let Ok(k) = key(&row) else {
            continue;
        };
        let mut e = entry(&row).unwrap();
        let first = date(s(&row, "firstSeen"))
            .or_else(|| date(s(card, "firstSeen")))
            .filter(|d| *d <= now);
        let checked = date(s(&row, "checkedAt"))
            .or_else(|| date(s(card, "checkedAt")))
            .filter(|d| *d <= now);
        let changed = date(s(&row, "priceChangedAt"))
            .or_else(|| date(s(card, "priceChangedAt")))
            .filter(|d| first.is_some_and(|f| f <= *d) && *d <= now);
        let last = date(s(&row, "lastSeen"))
            .or_else(|| date(s(card, "lastSeen")))
            .or(checked)
            .filter(|d| *d <= now);
        e["firstSeen"] = json!(stamp(first.unwrap_or(now)));
        e["firstSeenBasis"] = json!(if first.is_some() {
            row["firstSeenBasis"].as_str().unwrap_or("observed")
        } else {
            "migration_baseline"
        });
        e["lastSeen"] = json!(last.map(stamp));
        e["priceChangedAt"] = json!(changed.map(stamp));
        e["checkedAt"] = json!(checked.map(stamp));
        e["lastAttemptAt"] = json!(checked.map(stamp));
        e["lastCheckStatus"] = json!("migrated");
        e["active"] = json!(row["active"] != false);
        e["price"] = price(&json!({"price":row})).unwrap_or(Value::Null);
        e["snapshot"] = card.clone();
        history["offers"][k] = e;
    }
    history
}

pub fn load_history(path: &Path, now: Time, legacy: &[Value]) -> Result<Value> {
    if !path.exists() {
        return Ok(new_history(now, legacy));
    }
    let history = read_json(path)?;
    if history["schemaVersion"] != 1 || !history["offers"].is_object() {
        return Err(
            "Invalid Steam history; preserve and repair it instead of resetting freshness".into(),
        );
    }
    for (k, e) in history["offers"].as_object().unwrap() {
        if &key(e)? != k {
            return Err("Invalid Steam history identity".into());
        }
        for field in [
            "firstSeen",
            "lastSeen",
            "priceChangedAt",
            "checkedAt",
            "lastAttemptAt",
            "inactiveSince",
        ] {
            if !e[field].is_null() && date(s(e, field)).is_none() {
                return Err("Invalid Steam history timestamp".into());
            }
        }
        if e["active"] == true && (date(s(e, "firstSeen")).is_none() || !e["snapshot"].is_object())
        {
            return Err("Active Steam history is missing its original observation".into());
        }
    }
    Ok(history)
}

fn card(e: &Value) -> Option<Value> {
    if !e["snapshot"].is_object() {
        return None;
    }
    let first = date(s(e, "firstSeen"))?;
    let news_at = stamp(date(s(e, "priceChangedAt")).map_or(first, |d| d.max(first)));
    let mut card = e["snapshot"].clone();
    for field in ["firstSeen", "lastSeen", "priceChangedAt", "checkedAt"] {
        card[field] = e[field].clone();
    }
    card["freshnessAt"] = json!(news_at);
    card["publishedAt"] = Value::Null;
    if !card["deal"].is_object() {
        card["deal"] = json!({});
    }
    for field in [
        "appId",
        "storeRegion",
        "currency",
        "firstSeen",
        "firstSeenBasis",
        "lastSeen",
        "priceChangedAt",
        "checkedAt",
        "lastAttemptAt",
        "lastCheckStatus",
        "active",
        "inactiveSince",
    ] {
        card["deal"][field] = e[field].clone();
    }
    card["deal"]["newsAt"] = json!(news_at);
    if let Some(p) = e["price"].as_object() {
        for (k, v) in p {
            card["deal"][k] = v.clone();
        }
    }
    Some(card)
}

pub fn reconcile(history: &mut Value, observations: &[Value], now: Time) -> Result<Vec<Value>> {
    let when = stamp(now);
    let mut output = Vec::new();
    for original in observations {
        let mut row = original.clone();
        let (app, region, currency) = identity(&row)?;
        let k = key(&row)?;
        row["appId"] = json!(app);
        row["storeRegion"] = json!(region);
        row["currency"] = json!(currency);
        row["lastAttemptAt"] = json!(when);
        let p = price(&row);
        let deal = row["status"] == "deal"
            && p.as_ref()
                .is_some_and(|p| p["discountPercent"].as_i64().unwrap() > 0)
            && row["card"].is_object();
        let ended =
            row["status"] == "not_on_sale" && p.as_ref().is_some_and(|p| p["discountPercent"] == 0);
        if ["deal", "not_on_sale"].contains(&s(&row, "status")) && !(deal || ended) {
            row["status"] = json!("failed");
            row["error"] = json!("Incomplete or inconsistent price observation");
        }
        let mut e = history["offers"][&k].clone();
        if e.is_null() && (deal || ended) {
            e = entry(&row)?;
        }
        if e.is_object() {
            if date(s(&e, "lastAttemptAt")).is_some_and(|d| now < d) {
                row["status"] = json!("out_of_order");
            } else {
                e["lastAttemptAt"] = json!(when);
                e["lastCheckStatus"] = row["status"].clone();
                if deal || ended {
                    let p = p.as_ref().unwrap();
                    if e["firstSeen"].is_string()
                        && e["price"].is_object()
                        && (e["price"]["finalMinor"] != p["finalMinor"]
                            || e["price"]["discountPercent"] != p["discountPercent"])
                    {
                        e["priceChangedAt"] = json!(when);
                    }
                    e["price"] = p.clone();
                    e["checkedAt"] = json!(when);
                }
                if deal {
                    if e["firstSeen"].is_null() {
                        e["firstSeen"] = json!(when);
                        e["firstSeenBasis"] = json!("observed");
                    }
                    e["lastSeen"] = json!(when);
                    e["active"] = json!(true);
                    e["inactiveSince"] = Value::Null;
                    e["snapshot"] = row["card"].clone();
                } else if ended {
                    if e["active"] == true || e["inactiveSince"].is_null() {
                        e["inactiveSince"] = json!(when);
                    }
                    e["active"] = json!(false);
                }
            }
            if let Some(c) = card(&e) {
                row["card"] = c;
            } else {
                row.as_object_mut().unwrap().remove("card");
            }
            row["active"] = e["active"].clone();
            history["offers"][k] = e;
        } else {
            row.as_object_mut().unwrap().remove("card");
            row["active"] = json!(false);
        }
        output.push(row);
    }
    Ok(output)
}

pub fn visible(rows: &[Value], now: Time, window: i64) -> Vec<Value> {
    let mut seen = HashSet::new();
    rows.iter()
        .filter_map(|row| {
            let mut card = row["card"].clone();
            let deal = &card["deal"];
            if deal["active"] != true {
                return None;
            }
            let first = date(s(deal, "firstSeen"))?;
            let checked = date(s(deal, "checkedAt")).or_else(|| date(s(&card, "checkedAt")))?;
            let news_at = date(s(deal, "priceChangedAt")).map_or(first, |d| d.max(first));
            if checked > now
                || news_at > now
                || now - news_at >= Duration::hours(window)
                || date(s(deal, "saleEnd")).is_some_and(|end| end <= now)
                || !seen.insert(key(deal).ok()?)
            {
                return None;
            }
            card["freshnessAt"] = json!(stamp(news_at));
            card["deal"]["newsAt"] = json!(stamp(news_at));
            Some(card)
        })
        .collect()
}

pub fn observation(item: &Value, raw: &Value, now: Time) -> Result<Value> {
    let (app, region, currency) = identity(item)?;
    let when = stamp(now);
    let result = &raw[&app];
    if result["success"] != true {
        return Err("App data unavailable".into());
    }
    let data = &result["data"];
    let p = &data["price_overview"];
    let mut row =
        json!({"appId":app,"storeRegion":region,"currency":currency,"lastAttemptAt":when});
    if !p.is_object() || p["currency"] != currency {
        row["status"] = json!(if currency == "HKD" {
            "no_hkd_price"
        } else {
            "no_local_price"
        });
        return Ok(row);
    }
    let p = price(&json!({"price":{"finalMinor":p["final"],"initialMinor":p["initial"],"discountPercent":p["discount_percent"]}})).ok_or("Incomplete or inconsistent price response")?;
    row["price"] = p.clone();
    row["checkedAt"] = json!(when);
    let discount = p["discountPercent"].as_i64().unwrap();
    if discount == 0 {
        row["status"] = json!("not_on_sale");
        return Ok(row);
    }
    if !rows(&data["categories"]).iter().any(|c| c["id"] == 2) {
        return Err("Single-player category not confirmed".into());
    }
    if s(&data["price_overview"], "final_formatted").is_empty() {
        return Err("Formatted local price unavailable".into());
    }
    row["status"] = json!("deal");
    row["card"] = json!({"id":format!("steam-{}-{}-{app}",region.to_lowercase(),currency.to_lowercase()),"kind":"deal","category":"單機遊戲",
        "title":format!("{} 港區特價 -{discount}%",s(item,"name")),"summary":item["summary"],"source":"Steam 香港商店",
        "url":format!("https://store.steampowered.com/app/{app}/?cc={}",region.to_lowercase()),"publishedAt":null,"checkedAt":when,
        "imageUrl":data["header_image"],"imageLabel":format!("{} 官方商店封面",s(item,"name")),
        "deal":{"appId":app,"discountPercent":discount,"priceText":data["price_overview"]["final_formatted"],"originalPriceText":data["price_overview"]["initial_formatted"],
        "currency":currency,"storeRegion":region,"checkedAt":when,"finalMinor":p["finalMinor"],"initialMinor":p["initialMinor"]}});
    row["notice"] = json!(
        "API does not provide a verified sale end; check the store page before adding expiry."
    );
    Ok(row)
}

pub fn fetch(app: &App, now: Time, legacy: &[Value]) -> Result<()> {
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(app.root.join(".sites-runtime/steam-history.lock"))?;
    lock.try_lock()?;
    let path = app.root.join("data/steam-deal-history.json");
    let mut history = load_history(&path, now, legacy)?;
    let observations = parallel(rows(&app.watch), 4, |item| {
        app.get_json("steam",s(item,"appId"),&steam_url(app,item),5_000_000).and_then(|raw| observation(item,&raw,now)).unwrap_or_else(|_| json!({
            "appId":item["appId"],"storeRegion":item["storeRegion"].as_str().unwrap_or("HK"),"currency":item["currency"].as_str().unwrap_or("HKD"),
            "lastAttemptAt":stamp(now),"status":"failed","error":"Steam price check failed"}))
    });
    let candidates = reconcile(&mut history, &observations, now)?;
    write_json(&path, &history)?;
    write_json(
        &app.root.join(".sites-runtime/steam-deal-candidates.json"),
        &json!(candidates),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn durable_offer_freshness() {
        let fixture: Value =
            serde_json::from_str(include_str!("../scripts/fixtures/steam-lifecycle.json")).unwrap();
        let now = date(s(&fixture, "baselineAt")).unwrap();
        let observed = observation(&fixture["watchItem"], &fixture["discounted"], now).unwrap();
        let mut history = new_history(now, &[]);
        let rows = reconcile(&mut history, std::slice::from_ref(&observed), now).unwrap();
        assert_eq!(visible(&rows, now, 72).len(), 1);
        assert!(visible(&rows, now + Duration::hours(72), 72).is_empty());
        let rows = reconcile(
            &mut history,
            std::slice::from_ref(&observed),
            now + Duration::hours(96),
        )
        .unwrap();
        assert!(visible(&rows, now + Duration::hours(96), 72).is_empty());
        assert!(history["offers"]["123:HK:HKD"]["priceChangedAt"].is_null());
        let mut changed = observed;
        changed["price"]["finalMinor"] = json!(5000);
        let rows = reconcile(&mut history, &[changed], now + Duration::hours(97)).unwrap();
        assert_eq!(visible(&rows, now + Duration::hours(97), 72).len(), 1);
        let failed = json!({"appId":"123","status":"failed"});
        let rows = reconcile(&mut history, &[failed], now + Duration::hours(98)).unwrap();
        assert_eq!(
            rows[0]["card"]["checkedAt"],
            stamp(now + Duration::hours(97))
        );
        assert_eq!(rows[0]["card"]["firstSeen"], stamp(now));
    }
    #[test]
    fn history_round_trip_and_corruption() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("history.json");
        let now = date("2026-10-19T00:00:00Z").unwrap();
        let history = new_history(now, &[]);
        write_json(&path, &history).unwrap();
        assert_eq!(load_history(&path, now, &[]).unwrap(), history);
        std::fs::write(&path, "broken").unwrap();
        assert!(load_history(&path, now, &[]).is_err());
    }
}
