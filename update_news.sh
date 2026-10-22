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
checksum=$(jq -er '
  select(.status == "success" and .artifact == "dist/index.html")
  | .sha256 | select(type == "string" and test("^[0-9a-f]{64}$"))
' .sites-runtime/feeder-result.json)

# Verify the copied bytes so a concurrent manual rebuild cannot change the upload.
snapshot=$(mktemp .sites-runtime/pages-index.XXXXXX)
trap 'rm -f -- "$snapshot"' EXIT
cp -- dist/index.html "$snapshot"
printf '%s  %s\n' "$checksum" "$snapshot" | sha256sum --check --status

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
[[ -z "$(git -C "$publish_dir" ls-files -- ':!:index.html' ':!:.nojekyll')" ]]

cp -- "$snapshot" "$publish_dir/index.html"
: > "$publish_dir/.nojekyll"
git -C "$publish_dir" add -- index.html .nojekyll
if ! git -C "$publish_dir" diff --cached --quiet; then
  git -C "$publish_dir" \
    -c user.name=hilarycheng \
    -c user.email=65493+hilarycheng@users.noreply.github.com \
    -c commit.gpgsign=false commit --quiet -m 'Update news feed'
fi
# A previous push may have failed even when this edition has no changes.
git -C "$publish_dir" push --quiet "$repo_url" HEAD:refs/heads/main
printf '已推送：https://hilarycheng.github.io/newsfeed/\n'
