use serde_json::{Value, json};
use std::{fs, process::Command};

#[test]
fn console_modes_preserve_results_and_exit_codes() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    for dir in ["config", ".sites-runtime"] {
        fs::create_dir(root.join(dir)).unwrap();
    }
    let save = |name: &str, value: Value| {
        fs::write(root.join(name), serde_json::to_vec(&value).unwrap()).unwrap()
    };
    let now = chrono::Utc::now().to_rfc3339();
    save(
        "config/source-registry.json",
        json!([{"name":"Fixture","url":"https://example.com/feed","category":"Java / Kotlin","language":"en"}]),
    );
    save("config/source-holds.json", json!({"cve":"Offline fixture"}));
    for name in [
        "editorial-policy.json",
        "source-endpoints.json",
        "cve-policy.json",
    ] {
        save(&format!("config/{name}"), json!({}));
    }
    save("config/steam-deals.json", json!([]));
    let candidates: Vec<_> = (0..30).map(|i| json!({"source":"Fixture","title":format!("Java fixture {i}"),"feedExcerpt":"Synthetic fixture only.","url":format!("https://example.com/{i}"),"publishedAt":now})).collect();
    let statuses: Vec<_> = (0..10).map(|i| json!({"name":if i == 0 { "Fixture".to_string() } else { format!("Fixture {i}") },"status":"ok"})).collect();
    save(
        ".sites-runtime/candidates.json",
        json!({"fetchedAt":now,"candidates":candidates,"sourceStatus":statuses}),
    );
    save(
        ".sites-runtime/steam-deal-candidates.json",
        json!([{"status":"not_on_sale","lastAttemptAt":now}]),
    );
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_news-feeder"))
            .args(args)
            .current_dir(root)
            .env("PATH", "")
            .output()
            .unwrap()
    };

    let output = run(&["--from-cache"]);
    assert_eq!(
        output.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert_eq!(stdout.lines().count(), 1);
    assert_eq!(
        serde_json::from_str::<Value>(&stdout).unwrap()["status"],
        "success"
    );
    let stderr = String::from_utf8(output.stderr).unwrap();
    for message in [
        "Cache：",
        "RSS：",
        "篩選：",
        "CVE：",
        "Steam：",
        "Build：",
        "驗證：",
        "輸出位置：dist/index.html",
        "exit code 0",
    ] {
        assert!(stderr.contains(message), "Missing {message}: {stderr}");
    }
    for args in [["--quiet", "--from-cache"], ["--from-cache", "--quiet"]] {
        let output = run(&args);
        assert_eq!(output.status.code(), Some(0));
        assert!(output.stdout.is_empty());
        assert!(output.stderr.is_empty());
        let status: Value = serde_json::from_slice(
            &fs::read(root.join(".sites-runtime/feeder-result.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(status["status"], "success");
        assert_eq!(status["itemCount"], 30);
    }
    let snapshot = fs::read(root.join("data/feed.json")).unwrap();
    let build = run(&["--quiet", "--build-only"]);
    assert_eq!(build.status.code(), Some(0));
    assert!(build.stdout.is_empty() && build.stderr.is_empty());
    assert_eq!(fs::read(root.join("data/feed.json")).unwrap(), snapshot);
    let previous = fs::read(root.join("dist/index.html")).unwrap();
    assert_eq!(fs::read_dir(root.join("dist")).unwrap().count(), 6);
    let result: Value =
        serde_json::from_slice(&fs::read(root.join(".sites-runtime/feeder-result.json")).unwrap())
            .unwrap();
    use sha2::{Digest, Sha256};
    assert_eq!(result["sha256"], format!("{:x}", Sha256::digest(&previous)));

    let sources: Vec<_> = (1..=3)
        .map(|i| json!({"name":format!("Held {i}"),"url":format!("https://example.com/held-{i}")}))
        .collect();
    let holds: serde_json::Map<_, _> = (1..=3)
        .map(|i| {
            (
                format!("https://example.com/held-{i}"),
                json!("Offline fixture"),
            )
        })
        .collect();
    save("config/source-registry.json", json!(sources));
    save(
        "config/source-holds.json",
        json!({"rss":holds,"cve":"Offline fixture"}),
    );
    let output = run(&[]);
    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert_eq!(stdout.lines().count(), 1);
    let status: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(status["status"], "failure");
    assert_eq!(status["stage"], "rss_health");
    let stderr = String::from_utf8(output.stderr).unwrap();
    for count in 1..=3 {
        assert_eq!(
            stderr.matches(&format!("RSS [{count}/3]")).count(),
            1,
            "{stderr}"
        );
    }
    assert_eq!(stderr.matches("：略過").count(), 3);
    assert!(stderr.contains("失敗 [rss_health]："));
    assert!(stderr.contains("exit code 1"));
    assert_eq!(fs::read(root.join("dist/index.html")).unwrap(), previous);
    let output = run(&["--quiet"]);
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert!(output.stderr.is_empty());
    let status: Value =
        serde_json::from_slice(&fs::read(root.join(".sites-runtime/feeder-result.json")).unwrap())
            .unwrap();
    assert_eq!(status["status"], "failure");
    assert_eq!(fs::read(root.join("dist/index.html")).unwrap(), previous);
    for (arg, code) in [("--unknown", 1), ("--help", 0)] {
        let output = run(&["--quiet", arg]);
        assert_eq!(output.status.code(), Some(code));
        assert!(output.stdout.is_empty());
        assert!(output.stderr.is_empty());
    }
}
