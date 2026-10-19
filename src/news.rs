use crate::{
    common::*,
    sources::{App, SourceError, source_error},
};
use chrono::{Duration, FixedOffset, NaiveDate, TimeZone};
use regex::Regex;
use roxmltree::{Document, Node};
use serde_json::{Value, json};
use std::{
    collections::{BTreeSet, HashMap, HashSet},
    sync::LazyLock,
};

const BLOCK_GAMES: &str = r"(?i)\b(FPS|first.person shooter|Counter.Strike|Call of Duty|Battlefield|Valorant|Fortnite|PUBG|Apex Legends|Overwatch|Destiny 2|esports?|League of Legends|Dota|multiplayer.only|MMORPG|PS[3456]|PlayStation|Xbox|Nintendo|Switch 2|console)\b|第一人稱射擊|\u{7535}\u{7ade}|電競|主機獨佔";
const GAME_NAMES: &str = r"(?i)\b(Hades|Slay the Spire|Silksong|Stardew Valley|Baldur.?s Gate|Factorio|RimWorld|Terraria|Noita|Balatro|Dead Cells|Celeste|Hollow Knight|Civilization|Crusader Kings|Dwarf Fortress|Satisfactory|Against the Storm|Manor Lords|Persona|Obra Dinn|CloverPit)\b";
pub const MAJOR: &str = r"(?i)critical|actively exploit|exploited in the wild|remote code execution|重大|嚴重|積極利用|已遭利用";
pub const EXCLUDED: &str = r"(?i)\b(windows|microsoft|exchange|sharepoint|azure|office)\b";
pub const OTHER_LANGUAGE: &str =
    r"[\u{3040}-\u{30ff}\u{0400}-\u{04ff}\u{0600}-\u{06ff}\u{ac00}-\u{d7af}]";
pub static CVE_IDS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\bCVE-\d{4}-\d{4,}\b").unwrap());

pub fn feed_date(value: &str) -> Option<Time> {
    chrono::DateTime::parse_from_rfc2822(value)
        .ok()
        .map(|d| d.with_timezone(&chrono::Utc))
        .or_else(|| date(value))
}

fn child<'a, 'b>(node: Node<'a, 'b>, names: &[&str]) -> Option<Node<'a, 'b>> {
    node.children()
        .find(|n| n.is_element() && names.contains(&n.tag_name().name()))
}

fn text(node: Option<Node<'_, '_>>) -> String {
    node.map(|n| {
        n.descendants()
            .filter(|n| n.is_text())
            .filter_map(|n| n.text())
            .collect()
    })
    .unwrap_or_default()
}

pub fn ithome_date(markup: &str) -> Option<Time> {
    static RE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)<span\b[^>]*class=["']created["'][^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</span>"#)
            .unwrap()
    });
    let c = RE.captures(markup)?;
    let d = NaiveDate::parse_from_str(&c[1], "%Y-%m-%d")
        .ok()?
        .and_hms_opt(0, 0, 0)?;
    Some(
        FixedOffset::east_opt(8 * 3600)?
            .from_local_datetime(&d)
            .single()?
            .with_timezone(&chrono::Utc),
    )
}

fn recover_date(app: &App, url: &str) -> Option<Time> {
    app.target("ithome_metadata", "iThome", url).ok()?;
    let raw = app
        .get("ithome_metadata", "iThome", url, 2_000_000, 15)
        .ok()?;
    ithome_date(&String::from_utf8_lossy(&raw))
}

pub fn parse_feed(
    source: &Value,
    raw: &[u8],
    now: Time,
    recover: impl Fn(&str) -> Option<Time> + Sync,
) -> Result<Vec<Value>> {
    static ENCODING: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"(?i)<\?xml[^>]*encoding=["']([^"']+)"#).unwrap());
    let prefix = String::from_utf8_lossy(&raw[..raw.len().min(300)]);
    let encoding = encoding_rs::Encoding::for_bom(raw)
        .map(|(e, _)| e)
        .or_else(|| {
            ENCODING
                .captures(&prefix)
                .and_then(|c| encoding_rs::Encoding::for_label(c[1].as_bytes()))
        })
        .unwrap_or(encoding_rs::UTF_8);
    let (xml, _, bad) = encoding.decode(raw);
    if bad || xml.to_uppercase().contains("<!DOCTYPE") || xml.to_uppercase().contains("<!ENTITY") {
        return Err(source_error("invalid_feed_payload"));
    }
    let doc = Document::parse(xml.trim_start()).map_err(|_| source_error("invalid_feed_xml"))?;
    let root = doc.root_element();
    if !["rss", "feed", "rdf"].contains(&root.tag_name().name().to_lowercase().as_str()) {
        return Err(source_error("invalid_feed_payload"));
    }
    let entries: Vec<_> = root
        .descendants()
        .filter(|n| n.is_element() && ["item", "entry"].contains(&n.tag_name().name()))
        .take(200)
        .collect();
    let mut recovered = HashMap::new();
    if source["name"] == "iThome" {
        let urls: Vec<_> = entries
            .iter()
            .take(30)
            .filter_map(|n| {
                let d = text(child(*n, &["pubDate", "published", "date"]));
                let recent_date = d.get(..10).is_some_and(|d| {
                    matches(r"^\d{4}-\d{2}-\d{2}$", d)
                        && d >= (now - Duration::hours(96))
                            .format("%Y-%m-%d")
                            .to_string()
                            .as_str()
                });
                recent_date.then(|| text(child(*n, &["link"])))
            })
            .collect();
        for (url, d) in urls.iter().zip(parallel(&urls, 4, |url| recover(url))) {
            recovered.insert(url.clone(), d);
        }
    }
    let mut output = Vec::new();
    for n in entries {
        let title = plain(&text(child(n, &["title"])), usize::MAX);
        let date_raw = text(child(n, &["pubDate", "published", "date"]));
        let links: Vec<_> = n
            .children()
            .filter(|n| n.has_tag_name("link") || n.is_element() && n.tag_name().name() == "link")
            .collect();
        let url = links
            .iter()
            .find_map(|n| {
                (n.attribute("rel").unwrap_or("alternate") == "alternate")
                    .then(|| n.attribute("href"))
                    .flatten()
            })
            .map(str::to_string)
            .or_else(|| {
                links
                    .first()
                    .map(|n| text(Some(*n)))
                    .filter(|s| !s.is_empty())
            })
            .or_else(|| {
                n.attribute(("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "about"))
                    .map(str::to_string)
            })
            .unwrap_or_default();
        let normal = feed_date(&date_raw);
        let d = normal.or_else(|| recovered.get(&url).copied().flatten());
        let Some(d) = d else {
            continue;
        };
        if title.is_empty()
            || !url.starts_with("https://")
            || d > now
            || d < now - Duration::hours(72)
        {
            continue;
        }
        let mut description = text(child(n, &["description", "summary", "encoded", "content"]));
        if description.is_empty() {
            description = text(
                n.descendants()
                    .find(|n| n.is_element() && n.tag_name().name() == "description"),
            );
        }
        let summary = plain(
            &description,
            if source["preservePublisherText"] == true {
                usize::MAX
            } else {
                1800
            },
        );
        let mut media = n.descendants().find_map(|n| {
            (n.is_element() && ["thumbnail", "content"].contains(&n.tag_name().name()))
                .then(|| n.attribute("url"))
                .flatten()
                .filter(|u| u.starts_with("https://"))
                .map(str::to_string)
        });
        if media.is_none() {
            static IMG: LazyLock<Regex> =
                LazyLock::new(|| Regex::new(r#"<img[^>]+src=["'](https:[^"']+)"#).unwrap());
            let decoded = html_escape::decode_html_entities(&description);
            media = IMG.captures(&decoded).map(|c| c[1].to_string());
        }
        let publisher = child(n, &["source"]);
        let rights = plain(&text(child(n, &["rights", "copyright"])), usize::MAX);
        output.push(json!({"publisherRights":if rights.is_empty() { plain(&text(child(root,&["rights","copyright"])),usize::MAX) } else { rights },
            "publisherAuthor":plain(&text(child(n,&["creator","author"])),usize::MAX),"originalPublisher":plain(&text(publisher),usize::MAX),
            "originalPublisherUrl":publisher.and_then(|n| n.attribute("url")).unwrap_or(""),
            "id":&hash(url.as_bytes())[..24],"category":source["category"],"title":title,"source":source["name"],"sourceLanguage":source["language"],
            "preferredByUser":source["preferredByUser"] == true,"url":url,"publishedAt":stamp(d),
            "datePrecision":if normal.is_none() { "day" } else if matches(r"T\d|\d{1,2}:\d{2}",&date_raw) { "time" } else { "day" },
            "feedExcerpt":summary,"imageUrl":media,"discussionSource":source["discussionSource"] == true,"needsReview":true}));
    }
    Ok(output)
}

pub fn fetch_one(app: &App, source: &Value, now: Time) -> Value {
    if let Some(reason) = app.held_rss(source) {
        return json!({"source":{"name":source["name"],"status":"skipped","detail":reason},"candidates":[]});
    }
    let result = app
        .get("rss", s(source, "name"), s(source, "url"), 5_000_000, 25)
        .and_then(|raw| parse_feed(source, &raw, now, |u| recover_date(app, u)));
    match result {
        Ok(cards) => {
            json!({"source":{"name":source["name"],"status":"ok","candidateCount":cards.len(),"recoveredPublicationDates":cards.iter().filter(|a| a["datePrecision"] == "day" && source["name"] == "iThome").count()},"candidates":cards})
        }
        Err(e) => {
            let err = e.downcast_ref::<SourceError>();
            let code = err.map(|e| e.code).unwrap_or("source_processing_error");
            let mut status = json!({"name":source["name"],"sourceId":source["id"],"status":"failed","errorCode":code,"errorType":code,"detail":code});
            if let Some(status_code) = err.and_then(|e| e.http_status) {
                status["httpStatus"] = json!(status_code);
            }
            json!({"source":status,"candidates":[]})
        }
    }
}

pub fn fetch(app: &App, now: Time) -> Result<()> {
    let sources: Vec<_> = rows(&app.registry)
        .iter()
        .filter(|s| s["enabled"] != false)
        .cloned()
        .collect();
    let results = parallel(&sources, 12, |source| fetch_one(app, source, now));
    let mut seen = HashSet::new();
    let candidates: Vec<_> = results
        .iter()
        .flat_map(|r| rows(&r["candidates"]))
        .filter(|a| seen.insert(s(a, "url")))
        .cloned()
        .collect();
    let statuses: Vec<_> = results.iter().map(|r| r["source"].clone()).collect();
    write_json(
        &app.root.join(".sites-runtime/candidates.json"),
        &json!({"fetchedAt":stamp(now),"windowHours":72,"sourceStatus":statuses,"candidates":candidates}),
    )
}

pub fn medium(app: &App, a: &Value) -> bool {
    let url = reqwest::Url::parse(s(a, "url")).ok();
    let host = url.as_ref().and_then(|u| u.host_str()).unwrap_or("");
    rows(&app.policy["excludedMediumSourceNames"]).contains(&a["source"])
        || rows(&app.policy["excludedMediumDomains"])
            .iter()
            .filter_map(Value::as_str)
            .any(|d| host == d || host.ends_with(&format!(".{d}")))
}

pub fn normalize(app: &App, a: &Value, now: Time) -> Option<Value> {
    let empty = json!({});
    let source = rows(&app.registry)
        .iter()
        .find(|s| s["name"] == a["source"])
        .unwrap_or(&empty);
    let url = safe_url(s(a, "url"), true)?;
    if source["enabled"] == false || medium(app, a) {
        return None;
    }
    let language = source["language"]
        .as_str()
        .or(a["sourceLanguage"].as_str())
        .unwrap_or("en");
    let ifanr = source["id"] == "ifanr"
        && source["url"] == "https://www.ifanr.com/feed"
        && reqwest::Url::parse(&url)
            .ok()
            .and_then(|u| u.host_str().map(str::to_string))
            .is_some_and(|h| ["ifanr.com", "www.ifanr.com"].contains(&h.as_str()));
    if !["en", "zh-Hant", "zh-TW", "zh-HK"].contains(&language) && !(language == "zh-Hans" && ifanr)
    {
        return None;
    }
    if !current(s(a, "publishedAt"), now) {
        return None;
    }
    let preserve = source["preservePublisherText"] == true;
    let full_cve_text = format!(
        "{} {}",
        plain(s(a, "title"), usize::MAX),
        plain(s(a, "feedExcerpt"), usize::MAX)
    );
    let title = plain(s(a, "title"), if preserve { usize::MAX } else { 180 });
    let excerpt = plain(s(a, "feedExcerpt"), if preserve { usize::MAX } else { 350 });
    let text = format!("{title} {excerpt}");
    if title.is_empty() || matches(OTHER_LANGUAGE, &text) {
        return None;
    }
    let mut category = source["category"]
        .as_str()
        .or(a["category"].as_str())
        .unwrap_or("科技");
    let general = ["World / 國際", "香港"].contains(&category);
    let discussion = a["discussionSource"] == true;
    if !general
        && matches(
            r"(?i)\b(game|gaming|Steam|videogame|PlayStation|Xbox|Nintendo|FPS|esports)\b|遊戲|\u{6e38}\u{620f}|電競|\u{7535}\u{7ade}",
            &title,
        )
    {
        if matches(BLOCK_GAMES, &text) || !matches(GAME_NAMES, &title) {
            return None;
        }
        category = "單機遊戲";
    }
    if discussion && !matches(r"(?i)\b(show hn|ask hn)\b", &title) {
        return None;
    }
    if ["單機遊戲", "Steam 單機"].contains(&category)
        || ["pc_singleplayer_nonfps", "steam_app_allowlist"].contains(&s(source, "filter"))
    {
        if matches(BLOCK_GAMES, &text)
            || source["filter"] != "steam_app_allowlist" && !matches(GAME_NAMES, &title)
        {
            return None;
        }
        category = "單機遊戲";
    }
    if !general && matches(r"(?i)CVE-\d{4}-\d+|vulnerabilit|zero.day|漏洞", &title) {
        category = "資安";
    }
    if category == "資安"
        && (matches(EXCLUDED, &text)
            || !CVE_IDS.is_match(&full_cve_text)
            || !matches(MAJOR, &text)
            || !matches(
                r"(?i)open.?source|linux|java|mysql|apache|openssl|php|python|npm|redis|postgres|開源",
                &text,
            ))
    {
        return None;
    }
    if !["單機遊戲", "資安", "World / 國際", "香港"].contains(&category) {
        let elixir = matches(r"(?i)\b(elixir|erlang)\b", &text)
            || matches(r"(?i)\b(OTP|Phoenix|Hex|BEAM)\b", &text)
                && (source["category"] == "Elixir / Erlang"
                    || matches(
                        r"(?i)\b(functional programming|actor model|supervis(or|ion)|mix\s+(deps|compile|test)|LiveView|GenServer|Erlang VM|Elixir|Erlang)\b",
                        &text,
                    ));
        let topics = [
            (
                "Java / Kotlin",
                r"(?i)\b(java|kotlin|jvm|spring.?boot|spring framework|quarkus|gradle)\b",
            ),
            (
                "Rust / Node.js",
                r"(?i)\b(rust|node\.?js|nodejs|deno|bun|javascript|typescript)\b",
            ),
            (
                "Linux",
                r"(?i)\b(linux|ubuntu|debian|fedora|kernel|kde|gnome|wayland|open.?source)\b|開源|\u{5f00}\u{6e90}",
            ),
            (
                "Android / 嵌入式",
                r"(?i)\b(android|arduino|embedded|raspberry|microcontroller|esp32|stm32)\b|嵌入式",
            ),
            (
                "AI",
                r"(?i)\b(AI|LLM|machine learning|neural|chatgpt|claude|gemini|hugging.?face|ollama|agentic|transformer)\b|人工智能|人工智慧|大模型",
            ),
        ];
        if elixir {
            category = "Elixir / Erlang";
        } else if let Some((topic, _)) = topics.iter().find(|(_, p)| matches(p, &text)) {
            category = topic;
        } else if !rows(&app.policy["categories"])
            .iter()
            .any(|c| c == category)
            || source["discussionSource"] == true && source["preferredByUser"] != true
        {
            return None;
        }
    }
    if category == "AI"
        && matches(
            r"(?i)for beginners|complete guide|what is an? (ai|llm)|most beginners",
            &title,
        )
    {
        return None;
    }
    let summary = if excerpt.is_empty() {
        title.clone()
    } else if preserve {
        excerpt.clone()
    } else if matches("[A-Za-z]", &excerpt) && !matches(r"[\u{3400}-\u{9fff}]", &excerpt) {
        excerpt
            .split_whitespace()
            .take(25)
            .collect::<Vec<_>>()
            .join(" ")
    } else {
        cut(&excerpt, 100)
    };
    let mut card = json!({"id":&hash(url.as_bytes())[..24],"category":category,"title":title,"summary":summary,
        "summaryType":if excerpt.is_empty() { "title_only" } else { "publisher_feed_excerpt" },"source":a["source"],"url":url,
        "publishedAt":stamp(date(s(a,"publishedAt"))?),"datePrecision":a["datePrecision"].as_str().unwrap_or("time"),"sourceLanguage":language,"preferredByUser":source["preferredByUser"] == true});
    if preserve {
        card["preservePublisherText"] = json!(true);
    }
    for key in ["attributionUrl", "sourceBrandImage", "contentLicense"] {
        if source[key].is_string() {
            card[key] = source[key].clone();
        }
    }
    if category == "資安" {
        card["cveIds"] = json!(
            CVE_IDS
                .find_iter(&full_cve_text)
                .map(|m| m.as_str().to_uppercase())
                .collect::<BTreeSet<_>>()
        );
    }
    if source["contentLicense"].is_string() {
        for key in ["publisherRights", "publisherAuthor"] {
            if !s(a, key).is_empty() {
                card[key] = json!(plain(s(a, key), usize::MAX));
            }
        }
    }
    if !s(a, "originalPublisher").is_empty() {
        card["originalPublisher"] = json!(plain(s(a, "originalPublisher"), 120));
    }
    if let Some(u) = safe_url(s(a, "originalPublisherUrl"), true) {
        card["originalPublisherUrl"] = json!(u);
    }
    if discussion {
        card["summary"] = json!(format!(
            "[New discussion; timestamp is the discussion date] {}",
            cut(s(&card, "summary"), 290)
        ));
    }
    if let Some(image) = safe_url(s(a, "imageUrl"), true) {
        card["imageUrl"] = json!(image);
        card["imageLabel"] = json!("來源 RSS 配圖");
    }
    if url.contains("youtube.com/") {
        card["kind"] = json!("video");
    }
    Some(card)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rss_atom_and_topic_policy() {
        let now = date("2026-10-19T01:00:00Z").unwrap();
        let app = App::load(env!("CARGO_MANIFEST_DIR").into()).unwrap();
        let source = json!({"name":"Inside Java","category":"Java / Kotlin","language":"en"});
        for xml in [
            r#"<rss><channel><item><title>Java release</title><link>https://example.com/news</link><pubDate>Mon, 19 Oct 2026 00:00:00 +0000</pubDate><description>Useful release</description></item></channel></rss>"#,
            r#"<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Java release</title><link href="https://example.com/news"/><published>2026-10-19T00:00:00Z</published><summary>Useful release</summary></entry></feed>"#,
        ] {
            let cards = parse_feed(&source, xml.as_bytes(), now, |_| None).unwrap();
            assert_eq!(cards.len(), 1);
            let card = normalize(&app, &cards[0], now).unwrap();
            assert_eq!(card["summary"], "Useful release");
            assert_eq!(card["category"], "Java / Kotlin");
            let mut invalid = cards[0].clone();
            invalid["url"] = json!("https://medium.com/news");
            assert!(normalize(&app, &invalid, now).is_none());
            invalid = cards[0].clone();
            invalid["title"] = json!("Xbox game console launch");
            assert!(normalize(&app, &invalid, now).is_none());
        }
        assert!(parse_feed(&source, b"not XML", now, |_| None).is_err());
        assert!(parse_feed(&source, b"<!DOCTYPE rss><rss/>", now, |_| None).is_err());
        assert_eq!(
            ithome_date("<span class='created'>2026-10-19</span>"),
            date("2026-10-18T16:00:00Z")
        );
    }
}
