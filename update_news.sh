#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

case "$#:${1-}" in
  0:|1:--from-cache|1:--build-only) ;;
  *) printf '用法：update_news.sh [--from-cache | --build-only]\n' >&2; exit 2 ;;
esac

mkdir -p .sites-runtime
exec 9>.sites-runtime/publish.lock
flock -n -E 75 9
trap 'printf "更新／發布失敗（第 %s 行）。\n" "$LINENO" >&2' ERR

for tool in git ssh jq sha256sum; do
  command -v "$tool" >/dev/null
done
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND='ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15'

./news-feeder --quiet "$@"
files=(index.html sw.js manifest.webmanifest icon-192.png icon-512.png apple-touch-icon.png)
checksums=$(jq -er '
  select(.status == "success" and .artifact == "dist/index.html")
  | select(.artifacts | keys == ["apple-touch-icon.png", "icon-192.png", "icon-512.png", "index.html", "manifest.webmanifest", "sw.js"])
  | select(.sha256 == .artifacts["index.html"])
  | select(.artifacts | all(.[]; type == "string" and test("^[0-9a-f]{64}$")))
  | .artifacts | to_entries[] | "\(.value)  \(.key)"
' .sites-runtime/feeder-result.json)

# Verify the copied bytes so a concurrent manual rebuild cannot change the upload.
snapshot=$(mktemp -d .sites-runtime/pages-build.XXXXXX)
trap 'rm -rf -- "$snapshot"' EXIT
for file in "${files[@]}"; do
  cp -- "dist/$file" "$snapshot/$file"
done
(cd -- "$snapshot"; printf '%s\n' "$checksums" | sha256sum --check --status)

repo_url=git@hilary.git:hilarycheng/newsfeed.git
publish_dir="$PWD/.sites-runtime/pages"
if [[ ! -e "$publish_dir" ]]; then
  git clone --quiet "$repo_url" "$publish_dir"
  if ! git -C "$publish_dir" rev-parse --verify HEAD >/dev/null 2>&1; then
    git -C "$publish_dir" symbolic-ref HEAD refs/heads/main
  fi
fi

[[ -d "$publish_dir/.git" ]]
[[ "$(git -C "$publish_dir" config --get remote.origin.url)" == "$repo_url" ]]
[[ "$(git -C "$publish_dir" symbolic-ref --short HEAD)" == main ]]
git -C "$publish_dir" fetch --quiet origin
if git -C "$publish_dir" show-ref --verify --quiet refs/remotes/origin/main; then
  git -C "$publish_dir" merge --quiet --ff-only refs/remotes/origin/main
fi
excluded=(':!:.nojekyll')
for file in "${files[@]}"; do excluded+=(":!:$file"); done
[[ -z "$(git -C "$publish_dir" ls-files -- "${excluded[@]}")" ]]

for file in "${files[@]}"; do
  cp -- "$snapshot/$file" "$publish_dir/$file"
done
: > "$publish_dir/.nojekyll"
git -C "$publish_dir" add -- "${files[@]}" .nojekyll
if ! git -C "$publish_dir" diff --cached --quiet; then
  git -C "$publish_dir" \
    -c user.name=hilarycheng \
    -c user.email=65493+hilarycheng@users.noreply.github.com \
    -c commit.gpgsign=false commit --quiet -m 'Update news feed'
fi
# A previous push may have failed even when this edition has no changes.
git -C "$publish_dir" push --quiet "$repo_url" HEAD:refs/heads/main
printf '已推送：https://hilarycheng.github.io/newsfeed/\n'
