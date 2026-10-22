use crate::common::*;
use chrono::{Duration, Utc};
use serde_json::{Value, json};
use std::{collections::HashSet, fs, path::Path};

#[cfg(test)]
#[path = "render_tests.rs"]
mod tests;

const TEMPLATE: &str = include_str!("../app/index.html");
const CSS: &str = include_str!("../app/globals.css");
const READER: &str = include_str!("../app/reader.js");
const LICENSE: &str = include_str!("../public/licenses/Apache-2.0.txt");
const WORKER: &str = include_str!("../app/sw.js");
const PWA_ASSETS: &[(&str, &[u8])] = &[
    (
        "manifest.webmanifest",
        include_bytes!("../public/manifest.webmanifest"),
    ),
    ("icon-192.png", include_bytes!("../public/icon-192.png")),
    ("icon-512.png", include_bytes!("../public/icon-512.png")),
    (
        "apple-touch-icon.png",
        include_bytes!("../public/apple-touch-icon.png"),
    ),
];

fn escape(value: &str) -> String {
    html_escape::encode_double_quoted_attribute(value).into_owned()
}

fn story_time(story: &Value) -> Option<Time> {
    if story["kind"] != "deal" {
        return date(s(story, "publishedAt"));
    }
    date(s(&story["deal"], "newsAt"))
        .or_else(|| date(s(story, "freshnessAt")))
        .or_else(|| {
            ["firstSeen", "firstSeenAt", "priceChangedAt"]
                .iter()
                .filter_map(|key| date(s(&story["deal"], key)))
                .max()
        })
}

fn current_story(story: &Value, now: Time) -> bool {
    story_time(story).is_some_and(|at| {
        at <= now
            && if story["kind"] == "deal" {
                now - at < Duration::hours(72)
            } else {
                now - at <= Duration::hours(72)
            }
    })
}

pub fn prepare(feed: &Value, now: Time) -> Result<Value> {
    let updated = date(s(feed, "updatedAt")).ok_or("Invalid feed timestamp")?;
    chrono::NaiveDate::parse_from_str(s(feed, "editionDate"), "%Y-%m-%d")?;
    let stories = feed["stories"].as_array().ok_or("Invalid feed stories")?;
    if stories.is_empty() || stories.len() > 1200 {
        return Err("Invalid feed size".into());
    }
    let mut stories: Vec<_> = stories
        .iter()
        .filter(|story| {
            story["kind"] != "deal"
                || (story["deal"]["active"] != false
                    && current_story(story, now)
                    && !date(s(&story["deal"], "saleEnd")).is_some_and(|end| end <= now))
        })
        .cloned()
        .collect();
    let (mut ids, mut urls) = (HashSet::new(), HashSet::new());
    for story in &stories {
        if s(story, "id").is_empty()
            || s(story, "title").is_empty()
            || s(story, "summary").is_empty()
            || !ids.insert(s(story, "id"))
            || !urls.insert(s(story, "url"))
            || safe_url(s(story, "url"), false).is_none()
            || story_time(story).is_none()
        {
            return Err("Invalid or duplicate story".into());
        }
    }
    stories.sort_by(|a, b| {
        current_story(b, updated)
            .cmp(&current_story(a, updated))
            .then_with(|| story_time(b).cmp(&story_time(a)))
            .then_with(|| s(a, "id").cmp(s(b, "id")))
    });
    let mut feed = feed.clone();
    feed["stories"] = json!(stories);
    Ok(feed)
}

fn fields(input: &Value, names: &[&str]) -> Value {
    Value::Object(
        names
            .iter()
            .filter_map(|&name| {
                input
                    .get(name)
                    .filter(|v| !v.is_null())
                    .map(|v| (name.to_string(), v.clone()))
            })
            .collect(),
    )
}

fn payload(feed: &Value) -> Value {
    let stories: Vec<_> = rows(&feed["stories"])
        .iter()
        .map(|story| {
            // Publish only fields consumed by the reader; intake/state stays local.
            let mut out = fields(
                story,
                &[
                    "id",
                    "category",
                    "kind",
                    "title",
                    "summary",
                    "source",
                    "url",
                    "publishedAt",
                    "freshnessAt",
                    "checkedAt",
                    "datePrecision",
                    "sourceLanguage",
                    "originalLanguage",
                    "originalPublisher",
                    "originalPublisherUrl",
                    "attributionUrl",
                    "contentLicense",
                    "publisherAuthor",
                    "publisherRights",
                    "sourceBrandImage",
                    "imageUrl",
                    "imageLabel",
                    "cveIds",
                    "cveRating",
                ],
            );
            if story["deal"].is_object() {
                out["deal"] = fields(
                    &story["deal"],
                    &[
                        "newsAt",
                        "firstSeen",
                        "firstSeenAt",
                        "priceChangedAt",
                        "firstSeenBasis",
                        "checkedAt",
                        "active",
                        "discountPercent",
                        "priceText",
                        "originalPriceText",
                        "saleEnd",
                        "saleEndPrecision",
                        "lastCheckStatus",
                    ],
                );
            }
            for (name, keys) in [
                ("cvss", &["version", "score", "source", "vector"][..]),
                ("relatedArticles", &["url", "title", "source"][..]),
            ] {
                if !rows(&story[name]).is_empty() {
                    out[name] = json!(
                        rows(&story[name])
                            .iter()
                            .map(|v| fields(v, keys))
                            .collect::<Vec<_>>()
                    );
                }
            }
            out
        })
        .collect();
    json!({"updatedAt":feed["updatedAt"], "editionDate":feed["editionDate"], "stories":stories})
}

pub fn html(feed: &Value) -> Result<String> {
    let payload = serde_json::to_string(&payload(feed))?.replace('<', "\\u003c");
    let css = regex::Regex::new(r"(?s)/\*.*?\*/")?.replace_all(CSS, "");
    let css = css.lines().map(str::trim).collect::<String>();
    // Preserve JS newlines/ASI and string contents; compact data provides the main size saving.
    let reader = READER.lines().map(str::trim).collect::<Vec<_>>().join("\n");
    let favicon = include_str!("../public/favicon.svg")
        .trim()
        .bytes()
        .map(|b| format!("%{b:02X}"))
        .collect::<String>();
    let favicon = format!("data:image/svg+xml,{favicon}");
    let count = rows(&feed["stories"]).len().to_string();
    let edition_date = escape(s(feed, "editionDate"));
    let updated_at = date(s(feed, "updatedAt"))
        .ok_or("Invalid feed timestamp")?
        .with_timezone(&chrono::FixedOffset::east_opt(8 * 3600).unwrap())
        .format("%m/%d %H:%M")
        .to_string();
    let note = if s(feed, "note").is_empty() {
        String::new()
    } else {
        format!(
            "<aside class=\"notice\">{}</aside>",
            escape(s(feed, "note"))
        )
    };
    let mut failures = String::new();
    let failed: Vec<_> = rows(&feed["sourceStatus"])
        .iter()
        .filter(|s| s["status"] != "ok")
        .collect();
    if !failed.is_empty() {
        failures = format!(
            "<details class=\"source-status\"><summary>來源狀態 · {} 個來源受限</summary><ul>",
            failed.len()
        );
        for source in failed {
            let detail = source["detail"]
                .as_str()
                .filter(|s| !s.is_empty())
                .unwrap_or("來源暫時無法讀取");
            failures.push_str(&format!(
                "<li><strong>{}</strong>：{}</li>",
                escape(s(source, "name")),
                escape(detail)
            ));
        }
        failures.push_str("</ul></details>");
    }
    let license = if rows(&feed["stories"])
        .iter()
        .any(|s| s["contentLicense"] == "Apache-2.0")
    {
        format!(
            "<details id=\"apache-license\" class=\"source-status\"><summary>Apache-2.0 授權全文</summary><pre class=\"license-text\">{}</pre></details>",
            escape(LICENSE)
        )
    } else {
        String::new()
    };
    let fallback = rows(&feed["stories"]).iter().take(16).map(|story| format!(
        "<article class=\"story\"><div class=\"story-content\"><a class=\"story-link\" href=\"{}\" target=\"_blank\" rel=\"noopener noreferrer\"><h2>{}</h2></a><p class=\"summary\">{}</p><p class=\"source\">{}</p></div></article>",
        escape(s(story, "url")), escape(s(story, "title")), escape(s(story, "summary")), escape(s(story, "source")))).collect::<String>();
    let mut output = String::new();
    // Substitute only tokens in the trusted template, never tokens inside publisher content.
    for (index, part) in TEMPLATE.trim().split("{{").enumerate() {
        if index == 0 {
            output.push_str(part);
            continue;
        }
        let (key, tail) = part.split_once("}}").ok_or("Invalid HTML template")?;
        output.push_str(match key {
            "css" => &css,
            "reader" => &reader,
            "favicon" => &favicon,
            "payload" => &payload,
            "count" => &count,
            "edition_date" => &edition_date,
            "updated_at" => &updated_at,
            "note" => &note,
            "failures" => &failures,
            "license" => &license,
            "fallback" => &fallback,
            _ => return Err("Unknown HTML template token".into()),
        });
        output.push_str(tail);
    }
    Ok(output)
}

pub fn build(root: &Path, feed: &Value) -> Result<()> {
    let html = html(feed)?;
    write_bytes(&root.join("dist/index.html"), html.as_bytes())?;
    let mut version = html.into_bytes();
    version.extend_from_slice(WORKER.as_bytes());
    for (name, bytes) in PWA_ASSETS {
        write_bytes(&root.join("dist").join(name), bytes)?;
        version.extend_from_slice(bytes);
    }
    let worker = WORKER.replace("__CACHE_VERSION__", &hash(&version));
    write_bytes(&root.join("dist/sw.js"), worker.as_bytes())
}

pub fn artifact_hashes(root: &Path) -> Result<Value> {
    let mut hashes = serde_json::Map::new();
    for name in ["index.html", "sw.js"]
        .into_iter()
        .chain(PWA_ASSETS.iter().map(|(name, _)| *name))
    {
        hashes.insert(
            name.into(),
            json!(hash(&fs::read(root.join("dist").join(name))?)),
        );
    }
    Ok(Value::Object(hashes))
}

// Replaces the old standalone static builder without fetching or changing intake/state.
pub fn rebuild(root: &Path) -> Result<Value> {
    let runtime = root.join(".sites-runtime");
    fs::create_dir_all(&runtime)?;
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(runtime.join("feeder.lock"))?;
    lock.try_lock()?;
    let mut preserved = true;
    let result = (|| -> Result<Value> {
        let feed = prepare(&read_json(&root.join("data/feed.json"))?, Utc::now())?;
        let staging = tempfile::tempdir_in(root)?;
        build(staging.path(), &feed)?;
        let artifacts = artifact_hashes(staging.path())?;
        let dist = root.join("dist");
        let old = staging.path().join("old");
        if dist.exists() {
            fs::rename(&dist, &old)?;
        }
        if let Err(error) = fs::rename(staging.path().join("dist"), &dist) {
            if old.exists() && fs::rename(&old, &dist).is_err() {
                preserved = false;
                let saved = staging.keep();
                return Err(format!(
                    "Publish failed: {error}; previous output retained at {}",
                    saved.display()
                )
                .into());
            }
            return Err(error.into());
        }
        Ok(
            json!({"status":"success", "buildOnly":true, "itemCount":rows(&feed["stories"]).len(),
        "artifact":"dist/index.html", "sha256":artifacts["index.html"], "artifacts":artifacts, "modelCalls":0}),
        )
    })()
    .unwrap_or_else(|error| {
        json!({"status":"failure", "stage":"build", "buildOnly":true,
        "error":error.to_string(), "preservedPreviousEdition":preserved, "modelCalls":0})
    });
    write_json(&runtime.join("feeder-result.json"), &result)?;
    Ok(result)
}
