use chrono::{DateTime, Duration, SecondsFormat, Utc};
use regex::Regex;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    fs,
    io::Write,
    path::Path,
    sync::{LazyLock, Mutex},
    thread,
};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
pub type Time = DateTime<Utc>;

pub fn s<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("")
}

pub fn rows(value: &Value) -> &[Value] {
    value.as_array().map(Vec::as_slice).unwrap_or(&[])
}

pub fn read_json(path: &Path) -> Result<Value> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}

pub fn write_bytes(path: &Path, data: &[u8]) -> Result<()> {
    let parent = path.parent().ok_or("Missing parent directory")?;
    fs::create_dir_all(parent)?;
    let mut file = tempfile::NamedTempFile::new_in(parent)?;
    file.write_all(data)?;
    file.as_file().sync_all()?;
    file.persist(path)?;
    Ok(())
}

pub fn write_json(path: &Path, value: &Value) -> Result<()> {
    write_bytes(path, &serde_json::to_vec_pretty(value)?)
}

pub fn date(value: &str) -> Option<Time> {
    DateTime::parse_from_rfc3339(value)
        .ok()
        .map(|d| d.with_timezone(&Utc))
}

pub fn stamp(value: Time) -> String {
    value.to_rfc3339_opts(SecondsFormat::AutoSi, true)
}

pub fn recent(value: &str, now: Time) -> Result<()> {
    match date(value) {
        Some(d) if now - Duration::hours(1) <= d && d <= now => Ok(()),
        _ => Err("Intake cache outside one-hour regeneration window".into()),
    }
}

pub fn current(value: &str, now: Time) -> bool {
    date(value).is_some_and(|d| now - Duration::hours(72) <= d && d <= now)
}

pub fn hash(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

pub fn cut(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

pub fn matches(pattern: &str, value: &str) -> bool {
    static CACHE: LazyLock<Mutex<HashMap<String, Regex>>> = LazyLock::new(Mutex::default);
    let mut cache = CACHE.lock().unwrap();
    cache
        .entry(pattern.into())
        .or_insert_with(|| Regex::new(pattern).expect("valid internal regex"))
        .is_match(value)
}

pub fn plain(value: &str, limit: usize) -> String {
    static SCRIPT: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?is)<script\b[^>]*>.*?</script\s*>|<style\b[^>]*>.*?</style\s*>").unwrap()
    });
    static TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^>]*>|[\x00-\x1f\x7f]").unwrap());
    let value = SCRIPT.replace_all(value, " ");
    let value = html_escape::decode_html_entities(&value);
    let value = html_escape::decode_html_entities(&value);
    let value = TAG.replace_all(&value, " ");
    cut(
        &value.split_whitespace().collect::<Vec<_>>().join(" "),
        limit,
    )
}

pub fn safe_url(value: &str, strip_tracking: bool) -> Option<String> {
    if value
        .chars()
        .any(|c| c.is_whitespace() || c.is_control() || "<>\"\\".contains(c))
    {
        return None;
    }
    let mut url = reqwest::Url::parse(value).ok()?;
    let host = url.host_str()?;
    if url.scheme() != "https"
        || !url.username().is_empty()
        || url.password().is_some()
        || ["localhost", "127.0.0.1", "[::1]"].contains(&host)
        || host.ends_with(".local")
    {
        return None;
    }
    url.set_fragment(None);
    if strip_tracking {
        let pairs: Vec<_> = url
            .query_pairs()
            .filter(|(k, _)| {
                let k = k.to_lowercase();
                !k.starts_with("utm_") && !["fbclid", "gclid"].contains(&k.as_str())
            })
            .map(|(k, v)| (k.into_owned(), v.into_owned()))
            .collect();
        url.set_query(None);
        if !pairs.is_empty() {
            url.query_pairs_mut().extend_pairs(pairs);
        }
    }
    Some(url.into())
}

pub fn parallel<T: Sync, R: Send>(
    items: &[T],
    workers: usize,
    f: impl Fn(&T) -> R + Sync,
) -> Vec<R> {
    let mut output = Vec::new();
    for chunk in items.chunks(workers) {
        thread::scope(|scope| {
            let jobs: Vec<_> = chunk.iter().map(|item| scope.spawn(|| f(item))).collect();
            output.extend(
                jobs.into_iter()
                    .map(|job| job.join().expect("feeder worker panicked")),
            );
        });
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_and_canonicalize() {
        assert_eq!(
            plain("<script>bad()</script><p>Useful &amp; release</p>", 100),
            "Useful & release"
        );
        assert_eq!(
            safe_url("https://example.com/a?utm_source=x#top", true).as_deref(),
            Some("https://example.com/a")
        );
        for url in [
            "javascript:alert(1)",
            "http://example.com",
            "https://user:pass@example.com",
            "https://localhost/a",
        ] {
            assert!(safe_url(url, true).is_none());
        }
        let now = date("2026-10-19T00:00:00Z").unwrap();
        assert!(recent("2026-10-18T23:00:00Z", now).is_ok());
        assert!(recent("2026-10-18T22:59:59Z", now).is_err());
    }
}
