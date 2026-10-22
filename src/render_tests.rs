use super::*;
use std::{
    process::{Command, Stdio},
    time::{Duration as Wait, Instant},
};

fn fixture() -> Value {
    serde_json::from_str(include_str!("../scripts/fixtures/news-ui.json")).unwrap()
}

#[test]
fn deal_dates_boundaries_and_chronology() {
    let mut feed = fixture();
    let now = date(s(&feed, "updatedAt")).unwrap();
    let news = feed["stories"][0].clone();
    let deal = rows(&feed["stories"])
        .iter()
        .find(|s| s["kind"] == "deal")
        .unwrap()
        .clone();
    let mut deals = Vec::new();
    for (name, hours) in [
        ("inside", 71),
        ("boundary", 72),
        ("old", 73),
        ("future", -1),
        ("inactive", 1),
        ("expired", 1),
    ] {
        let mut d = deal.clone();
        d["id"] = json!(name);
        d["url"] = json!(format!("https://example.com/{name}"));
        d["deal"]["newsAt"] = json!(stamp(now - Duration::hours(hours)));
        if name == "inactive" {
            d["deal"]["active"] = json!(false);
        }
        if name == "expired" {
            d["deal"]["saleEnd"] = json!(stamp(now));
        }
        deals.push(d);
    }
    feed["stories"] = json!([deals, vec![news]].concat());
    let built = prepare(&feed, now).unwrap();
    assert_eq!(
        rows(&built["stories"])
            .iter()
            .map(|story| s(story, "id"))
            .collect::<Vec<_>>(),
        vec!["tech-0", "inside"]
    );
    let mut d = deal;
    d["deal"]["newsAt"] = Value::Null;
    d["freshnessAt"] = Value::Null;
    d["deal"]["firstSeen"] = Value::Null;
    d["checkedAt"] = json!(stamp(now));
    assert!(
        story_time(&d).is_none(),
        "Price checks must not create news dates"
    );
    d["deal"]["firstSeenAt"] = json!(stamp(now - Duration::hours(4)));
    d["deal"]["priceChangedAt"] = json!(stamp(now - Duration::hours(1)));
    assert_eq!(story_time(&d), Some(now - Duration::hours(1)));
}

#[test]
fn publication_is_safe_compact_and_self_contained() {
    let mut input = fixture();
    input["stories"][0]["privateFixtureField"] = json!("must-stay-local");
    input["note"] = json!("{{reader}} </script><img onerror=alert(1)>");
    let feed = prepare(&input, date(s(&input, "updatedAt")).unwrap()).unwrap();
    let page = html(&feed).unwrap();
    assert_eq!(page.matches("<script").count(), 2);
    assert!(!page.contains("<script src="));
    assert!(!page.contains("rel=\"stylesheet\""));
    assert!(!page.contains("@import"));
    assert!(!page.contains("must-stay-local"));
    assert!(page.contains("href=\"data:image/svg+xml,"));
    assert!(page.contains("Apache-2.0 授權全文"));
    assert!(page.contains("{{reader}} &lt;/script&gt;"));
    let json = page
        .split("id=\"feed-data\">")
        .nth(1)
        .unwrap()
        .split("</script>")
        .next()
        .unwrap();
    assert!(!json.contains('<'));
    let data: Value = serde_json::from_str(json).unwrap();
    assert_eq!(
        data["stories"][1]["summary"],
        input["stories"][1]["summary"]
    );
    assert_eq!(data["stories"].as_array().unwrap().len(), 91);
    assert!(
        !json.contains("\\u003carticle"),
        "Do not store repeated card markup"
    );
    assert!(
        page.len() < 150_000,
        "Fixture size regressed: {}",
        page.len()
    );
    for bad in [
        "javascript:alert(1)",
        "https://user:pass@example.com",
        "https://localhost/a",
    ] {
        let mut invalid = input.clone();
        invalid["stories"][0]["url"] = json!(bad);
        assert!(prepare(&invalid, date(s(&input, "updatedAt")).unwrap()).is_err());
    }
    let mut duplicate = input.clone();
    duplicate["stories"][1]["id"] = duplicate["stories"][0]["id"].clone();
    assert!(prepare(&duplicate, date(s(&input, "updatedAt")).unwrap()).is_err());
}

#[test]
fn rebuild_preserves_snapshot_and_old_output_on_invalid_input() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let feed = fixture();
    write_json(&root.join("data/feed.json"), &feed).unwrap();
    let source = fs::read(root.join("data/feed.json")).unwrap();
    write_bytes(&root.join("dist/old.css"), b"old").unwrap();
    let result = rebuild(root).unwrap();
    assert_eq!(fs::read(root.join("data/feed.json")).unwrap(), source);
    assert_eq!(fs::read_dir(root.join("dist")).unwrap().count(), 1);
    let output = fs::read(root.join("dist/index.html")).unwrap();
    assert_eq!(result["sha256"], hash(&output));
    write_bytes(&root.join("data/feed.json"), b"broken json").unwrap();
    assert_eq!(rebuild(root).unwrap()["status"], "failure");
    assert_eq!(
        read_json(&root.join(".sites-runtime/feeder-result.json")).unwrap()["status"],
        "failure"
    );
    assert_eq!(fs::read(root.join("dist/index.html")).unwrap(), output);
}

#[test]
#[ignore = "requires a local Chrome or Chromium browser; no Node.js"]
fn browser_reader() {
    let temp = tempfile::tempdir().unwrap();
    let feed = fixture();
    let page = html(&prepare(&feed, date(s(&feed, "updatedAt")).unwrap()).unwrap()).unwrap();
    let page_json = serde_json::to_string(&page)
        .unwrap()
        .replace('<', "\\u003c");
    let harness = format!(
        "<!doctype html><meta charset=utf-8><title>Reader test</title><iframe id=preview style=\"border:0;width:1920px;height:1000px\"></iframe><pre id=result>pending</pre><script>const fixturePage={page_json};{}</script>",
        include_str!("../tests/reader.js").replace("</script", "<\\/script")
    );
    let path = temp.path().join("reader-test.html");
    fs::write(&path, harness).unwrap();
    let stdout = fs::File::create(temp.path().join("stdout")).unwrap();
    let stderr = fs::File::create(temp.path().join("stderr")).unwrap();
    let browser = std::env::var("CHROME_BIN").unwrap_or_else(|_| "google-chrome".into());
    let mut child = Command::new(browser)
        .args([
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-background-networking",
            "--no-proxy-server",
            "--host-resolver-rules=MAP * ~NOTFOUND",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1920,1080",
            "--hide-scrollbars",
            "--virtual-time-budget=5000",
            "--dump-dom",
        ])
        .arg(format!(
            "--user-data-dir={}",
            temp.path().join("profile").display()
        ))
        .arg(format!("file://{}", path.display()))
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(stderr)
        .spawn()
        .unwrap();
    let start = Instant::now();
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(
                status.success(),
                "Chrome failed: {}",
                fs::read_to_string(temp.path().join("stderr")).unwrap()
            );
            break;
        }
        if start.elapsed() > Wait::from_secs(40) {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("Browser test timed out");
        }
        std::thread::sleep(Wait::from_millis(50));
    }
    let output = fs::read_to_string(temp.path().join("stdout")).unwrap();
    let result = output
        .split("<pre id=\"result\">")
        .nth(1)
        .unwrap_or(&output)
        .split("</pre>")
        .next()
        .unwrap();
    assert_eq!(
        result,
        "passed",
        "Browser result: {result}\n{}",
        fs::read_to_string(temp.path().join("stderr")).unwrap()
    );
}
