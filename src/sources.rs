use crate::common::*;
use reqwest::{Url, blocking::Client, redirect::Policy};
use serde_json::{Value, json};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::PathBuf,
    sync::Mutex,
    time::Duration,
};

pub struct App {
    pub root: PathBuf,
    pub registry: Value,
    pub policy: Value,
    pub holds: Value,
    pub watch: Value,
    pub endpoints: Value,
    pub cve_policy: Value,
    client: Client,
}

#[derive(Debug)]
pub struct SourceError {
    pub code: &'static str,
    pub http_status: Option<u16>,
}
impl std::fmt::Display for SourceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.code)
    }
}
impl std::error::Error for SourceError {}

pub fn source_error(code: &'static str) -> Box<dyn std::error::Error + Send + Sync> {
    Box::new(SourceError {
        code,
        http_status: None,
    })
}

pub fn steam_url(app: &App, item: &Value) -> String {
    let region = item["storeRegion"].as_str().unwrap_or("HK").to_lowercase();
    format!(
        "{}?appids={}&cc={region}&l=english",
        s(&app.endpoints, "steamApi"),
        s(item, "appId")
    )
}

pub fn public_https(value: &str) -> Result<Url> {
    if value
        .chars()
        .any(|c| c.is_whitespace() || c.is_control() || c == '\\')
    {
        return Err(source_error("invalid_url"));
    }
    let url = Url::parse(value).map_err(|_| source_error("invalid_url"))?;
    let host = url.host_str().unwrap_or("");
    if url.scheme() != "https"
        || host.is_empty()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || url.port().is_some_and(|p| p != 443)
    {
        return Err(source_error("https_public_host_required"));
    }
    if host.ends_with('.')
        || !host.contains('.')
        || host == "localhost.localdomain"
        || [
            ".local",
            ".localhost",
            ".internal",
            ".lan",
            ".home",
            ".test",
            ".invalid",
        ]
        .iter()
        .any(|x| host.ends_with(x))
        || !matches(r"\.[A-Za-z]{2,63}$", host)
    {
        return Err(source_error("private_or_reserved_host"));
    }
    if host.parse::<std::net::IpAddr>().is_ok() || host.starts_with('[') {
        return Err(source_error("ip_literal_not_registered"));
    }
    Ok(url)
}

impl App {
    pub fn load(root: PathBuf) -> Result<Self> {
        let holds_path = root.join("config/source-holds.json");
        let holds = if holds_path.exists() {
            read_json(&holds_path)?
        } else {
            json!({})
        };
        let registry = read_json(&root.join("config/source-registry.json"))?;
        let watch = read_json(&root.join("config/steam-deals.json"))?;
        if !holds.is_object() || !registry.is_array() || !watch.is_array() {
            return Err("Invalid source configuration".into());
        }
        Ok(Self {
            policy: read_json(&root.join("config/editorial-policy.json"))?,
            endpoints: read_json(&root.join("config/source-endpoints.json"))?,
            cve_policy: read_json(&root.join("config/cve-policy.json"))?,
            registry,
            holds,
            watch,
            root,
            client: Client::builder()
                .redirect(Policy::none())
                .timeout(Duration::from_secs(25))
                .build()?,
        })
    }

    pub fn held_host<'a>(&'a self, value: &str) -> Option<&'a str> {
        let url = Url::parse(value).ok()?;
        self.holds["deniedHosts"][url.host_str()?.to_lowercase().trim_end_matches('.')].as_str()
    }

    pub fn held_rss<'a>(&'a self, source: &Value) -> Option<&'a str> {
        self.held_host(s(source, "url"))
            .or_else(|| self.holds["rss"][s(source, "url")].as_str())
    }

    pub fn target(&self, adapter: &str, identity: &str, value: &str) -> Result<String> {
        let url = public_https(value)?;
        match adapter {
            "rss" => {
                if rows(&self.registry)
                    .iter()
                    .any(|s| s["enabled"] != false && s["name"] == identity && s["url"] == value)
                {
                    return Ok(value.into());
                }
            }
            "ithome_metadata" => {
                if identity == "iThome"
                    && url.query().is_none()
                    && value
                        .strip_prefix(s(&self.endpoints, "ithomeMetadataBase"))
                        .is_some_and(|p| matches(r"^[0-9]+$", p))
                    && let Some(source) = rows(&self.registry)
                        .iter()
                        .find(|s| s["enabled"] != false && s["name"] == "iThome")
                {
                    return Ok(s(source, "url").into());
                }
            }
            "steam" => {
                if rows(&self.watch)
                    .iter()
                    .any(|item| s(item, "appId") == identity && steam_url(self, item) == value)
                {
                    return Ok(value.into());
                }
            }
            "cve" if identity == "CVE List V5" => {
                if value == s(&self.endpoints, "cveRevision") {
                    return Ok(value.into());
                }
                let base = s(&self.endpoints, "cveBase");
                let (prefix, suffix) = base.split_once("/main/").ok_or("Invalid CVE base URL")?;
                let pinned = format!(
                    r"{}/[0-9a-f]{{40}}/{}",
                    regex::escape(prefix),
                    regex::escape(suffix)
                );
                if matches(&format!(r"^{pinned}deltaLog\.json$"), value) {
                    return Ok(value.into());
                }
                let re = regex::Regex::new(&format!(
                    r"^{pinned}([0-9]{{4}})/([0-9]+)xxx/(CVE-([0-9]{{4}})-([0-9]{{4,19}}))\.json$"
                ))?;
                if let Some(c) = re.captures(value)
                    && c[1] == c[4]
                    && c[2] == c[5][..c[5].len() - 3]
                {
                    return Ok(value.into());
                }
            }
            _ => (),
        }
        Err(source_error("unregistered_destination"))
    }

    pub fn authorize(&self, adapter: &str, identity: &str, url: &str) -> Result<()> {
        if self.holds["networkRefreshHold"]
            .as_str()
            .is_some_and(|v| !v.is_empty())
        {
            return Err(source_error("network_refresh_held"));
        }
        let source_url = self.target(adapter, identity, url)?;
        if self.held_host(url).is_some() {
            return Err(source_error("hostname_held"));
        }
        if ["rss", "ithome_metadata"].contains(&adapter)
            && self.holds["rss"][source_url].is_string()
        {
            return Err(source_error("source_held"));
        }
        if adapter == "cve" && self.holds["cve"].is_string() {
            return Err(source_error("cve_held"));
        }
        Ok(())
    }

    pub fn audit(
        &self,
        adapter: &str,
        identity: &str,
        url: &str,
        outcome: &str,
        status: Option<u16>,
    ) -> Result<()> {
        static LOCK: Mutex<()> = Mutex::new(());
        let _guard = LOCK.lock().unwrap();
        let mut url = Url::parse(url)?;
        let keys: Vec<_> = url.query_pairs().map(|(k, _)| k.into_owned()).collect();
        url.set_username("").ok();
        url.set_password(None).ok();
        url.set_fragment(None);
        url.set_query(None);
        if !keys.is_empty() {
            url.query_pairs_mut()
                .extend_pairs(keys.iter().map(|k| (k.as_str(), "[redacted]")));
        }
        fs::create_dir_all(self.root.join(".sites-runtime"))?;
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.root.join(".sites-runtime/source-request-audit.jsonl"))?;
        writeln!(
            file,
            "{}",
            json!({"at":stamp(chrono::Utc::now()),"adapter":adapter,"source":identity,"url":cut(url.as_str(),2048),"outcome":outcome,"httpStatus":status})
        )?;
        Ok(())
    }

    pub fn get(
        &self,
        adapter: &str,
        identity: &str,
        url: &str,
        limit: u64,
        seconds: u64,
    ) -> Result<Vec<u8>> {
        if let Err(e) = self.authorize(adapter, identity, url) {
            self.audit(adapter, identity, url, "blocked", None)?;
            return Err(e);
        }
        self.audit(adapter, identity, url, "authorized_initial_url", None)?;
        let result = (|| -> Result<Vec<u8>> {
            let response = self.client.get(url).header("User-Agent","NewsFeed/1.0").header("Accept","application/rss+xml, application/atom+xml, application/xml, application/json, text/xml, */*")
                .timeout(Duration::from_secs(seconds)).send().map_err(|_| source_error("transport_error"))?;
            let status = response.status().as_u16();
            self.audit(adapter, identity, url, "response", Some(status))?;
            if (300..400).contains(&status) {
                return Err(Box::new(SourceError {
                    code: "redirect_blocked",
                    http_status: Some(status),
                }));
            }
            if status != 200 {
                return Err(Box::new(SourceError {
                    code: "http_error",
                    http_status: Some(status),
                }));
            }
            let mut body = Vec::new();
            response
                .take(limit + 1)
                .read_to_end(&mut body)
                .map_err(|_| source_error("transport_error"))?;
            if body.len() as u64 > limit {
                return Err(source_error("response_size_limit"));
            }
            Ok(body)
        })();
        if result.is_err() {
            self.audit(adapter, identity, url, "request_failed", None)?;
        }
        result
    }

    pub fn get_json(&self, adapter: &str, identity: &str, url: &str, limit: u64) -> Result<Value> {
        Ok(serde_json::from_slice(
            &self.get(adapter, identity, url, limit, 20)?,
        )?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn holds_and_registered_destinations() {
        let app = App::load(PathBuf::from(env!("CARGO_MANIFEST_DIR"))).unwrap();
        assert!(
            app.authorize("rss", "Inside Java", "https://inside.java/feed.xml")
                .is_ok()
        );
        assert!(
            app.authorize("rss", "Inside Java", "https://inside.java/other")
                .is_err()
        );
        assert!(
            app.authorize("cve", "CVE List V5", s(&app.endpoints, "cveRevision"))
                .is_err()
        );
        assert!(
            app.authorize("rss", "Unknown", "https://127.0.0.1/feed")
                .is_err()
        );
        assert!(app.held_host("https://bun.sh/feed").is_some());
        let url = format!(
            "https://raw.githubusercontent.com/CVEProject/cvelistV5/{}/cves/2026/12xxx/CVE-2026-12345.json",
            "a".repeat(40)
        );
        assert!(app.target("cve", "CVE List V5", &url).is_ok());
    }
}
