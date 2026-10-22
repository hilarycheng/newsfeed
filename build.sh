#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
cargo build --release --locked "$@"
cp -- target/release/news-feeder ./news-feeder
printf 'Built: %s/news-feeder\n' "$PWD"
