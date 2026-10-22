mod common;
mod cve;
mod edition;
mod news;
mod render;
mod sources;
mod steam;

use common::*;
use serde_json::json;

fn main() {
    let mut args: Vec<_> = std::env::args().skip(1).collect();
    let quiet = args.iter().any(|arg| arg == "--quiet");
    QUIET.store(quiet, std::sync::atomic::Ordering::Relaxed);
    args.retain(|arg| arg != "--quiet");
    if args == ["--help"] || args == ["-h"] {
        if !quiet {
            println!(
                "news-feeder [--from-cache | --build-only] [--quiet]\nRun from the project directory. Output: dist/index.html and one JSON status.\n--build-only: rebuild the saved edition without fetching sources.\n--quiet: no console output; exit code and result files are preserved."
            );
        }
        return;
    }
    let result = (|| -> Result<_> {
        if !args.is_empty() && args != ["--from-cache"] && args != ["--build-only"] {
            return Err("Usage: news-feeder [--from-cache | --build-only] [--quiet]".into());
        }
        let root = std::env::current_dir()?;
        if args == ["--build-only"] {
            return render::rebuild(&root);
        }
        progress(format_args!("開始：讀取設定（{}）", root.display()));
        let app = sources::App::load(root)?;
        edition::run(&app, args == ["--from-cache"])
    })()
    .unwrap_or_else(
        |e| json!({"status":"failure", "stage":"preflight", "error":e.to_string(), "modelCalls":0}),
    );
    if !quiet {
        println!("{result}");
    }
    if result["status"] != "success" {
        progress(format_args!(
            "失敗 [{}]：{}",
            s(&result, "stage"),
            s(&result, "error")
        ));
        progress(format_args!("程式結束（exit code 1）。"));
        std::process::exit(1);
    }
    progress(format_args!(
        "完成：{} 則內容。輸出位置：{}（專案目錄）。",
        result["itemCount"],
        s(&result, "artifact")
    ));
    progress(format_args!("程式結束（exit code 0）。"));
}
