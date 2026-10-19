mod common;
mod cve;
mod edition;
mod news;
mod sources;
mod steam;

use common::*;
use serde_json::json;

fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["--help"] || args == ["-h"] {
        println!(
            "news-feeder [--from-cache]\nRun from the project directory. Output: dist/ and one JSON status."
        );
        return;
    }
    let result = (|| -> Result<_> {
        if !args.is_empty() && args != ["--from-cache"] {
            return Err("Usage: news-feeder [--from-cache]".into());
        }
        let app = sources::App::load(std::env::current_dir()?)?;
        edition::run(&app, args == ["--from-cache"])
    })()
    .unwrap_or_else(
        |e| json!({"status":"failure", "stage":"preflight", "error":e.to_string(), "modelCalls":0}),
    );
    println!("{result}");
    if result["status"] != "success" {
        std::process::exit(1);
    }
}
