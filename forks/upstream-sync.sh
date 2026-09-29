#!/usr/bin/env bash
# Keep forked third-party skills in step with their upstream. Each fork folder holds an
# UPSTREAM file (repo, path, base commit); base is the upstream commit the fork last took in.
#
#   upstream-sync.sh check           list forks whose upstream path has moved past base
#   upstream-sync.sh check --hook    same, but at most once a day and silent when all is current
#   upstream-sync.sh merge <fork>    three-way merge base -> upstream into the fork, move base
set -euo pipefail

forks="$(cd "$(dirname "$0")" && pwd)"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/upstream-sync"
mkdir -p "$cache"

field() { sed -n "s/^$2=//p" "$1/UPSTREAM"; }

# Upstream commits touching the fork's path since base, newest first, as "sha subject".
pending() {
  local dir=$1 repo path base
  repo=$(field "$dir" repo) path=$(field "$dir" path) base=$(field "$dir" base)
  gh api "repos/$repo/commits?path=$path&per_page=50" \
    --jq '.[] | .sha + " " + (.commit.message | split("\n")[0])' |
    awk -v base="$base" '$1 == base { exit } { print }'
}

check() {
  local hook=${1:-} stamp="$cache/last-check" found=0
  if [ "$hook" = --hook ]; then
    [ -n "$(find "$stamp" -mtime -1 2>/dev/null)" ] && exit 0
    gh auth status >/dev/null 2>&1 || exit 0
  fi
  for f in "$forks"/*/UPSTREAM; do
    [ -e "$f" ] || continue
    local dir name commits
    dir=$(dirname "$f") name=$(basename "$dir")
    commits=$(pending "$dir" 2>/dev/null) || { [ "$hook" = --hook ] && exit 0; echo "$name: upstream unreachable" >&2; continue; }
    if [ -n "$commits" ]; then
      found=1
      echo "Forked skill '$name' is $(wc -l <<<"$commits" | tr -d ' ') upstream commit(s) behind $(field "$dir" repo):"
      sed 's/^\([0-9a-f]\{10\}\)[0-9a-f]* /  \1 /' <<<"$commits"
    fi
  done
  touch "$stamp"
  if [ "$found" = 1 ]; then
    echo "Merge with: $forks/upstream-sync.sh merge <fork>, then resolve any conflict markers."
  elif [ "$hook" != --hook ]; then
    echo "All forks match upstream."
  fi
}

merge() {
  local name=${1:?usage: upstream-sync.sh merge <fork>} dir repo path base head clone
  dir="$forks/$name"
  [ -f "$dir/UPSTREAM" ] || { echo "No fork called $name" >&2; exit 1; }
  if [ -n "$(git -C "$forks" status --porcelain -- "$dir")" ]; then
    echo "Commit or stash the changes in $dir first, so the merge can be reviewed and undone." >&2
    exit 1
  fi
  repo=$(field "$dir" repo) path=$(field "$dir" path) base=$(field "$dir" base)

  clone="$cache/${repo//\//__}"
  if [ -d "$clone" ]; then
    git -C "$clone" fetch -q origin
  else
    git clone -q --filter=blob:none --no-checkout "https://github.com/$repo.git" "$clone"
  fi
  # The newest commit touching the path, so check's walk back from upstream finds it as base.
  head=$(git -C "$clone" log -1 --format=%H origin/HEAD -- "$path")
  [ "$head" = "$base" ] && { echo "$name already matches upstream."; exit 0; }

  local conflicts=() file
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  while IFS=$'\t' read -r status file; do
    local rel=${file#"$path"/} ours="$dir/${file#"$path"/}"
    case $status in
      A)
        if [ -e "$ours" ]; then conflicts+=("$rel (added upstream and here)"); else
          mkdir -p "$(dirname "$ours")"
          git -C "$clone" show "$head:$file" >"$ours"
        fi ;;
      D)
        conflicts+=("$rel (deleted upstream; delete it here if nothing of ours needs it)") ;;
      *)
        git -C "$clone" show "$base:$file" >"$tmp/base"
        git -C "$clone" show "$head:$file" >"$tmp/theirs"
        [ -e "$ours" ] || { conflicts+=("$rel (changed upstream, deleted here)"); continue; }
        git merge-file -L fork -L "base ${base:0:10}" -L "upstream ${head:0:10}" \
          "$ours" "$tmp/base" "$tmp/theirs" || conflicts+=("$rel")
        ;;
    esac
  done < <(git -C "$clone" diff --no-renames --name-status "$base" "$head" -- "$path")

  sed -i '' "s/^base=.*/base=$head/" "$dir/UPSTREAM" 2>/dev/null || sed -i "s/^base=.*/base=$head/" "$dir/UPSTREAM"
  echo "Merged $name up to ${head:0:10}. Upstream changes, for context:"
  git -C "$clone" log --format='  %h %s' "$base..$head" -- "$path"
  if [ ${#conflicts[@]} -gt 0 ]; then
    echo "Needs resolving by hand:"
    printf '  %s\n' "${conflicts[@]}"
  fi
  echo "Review with: git -C $forks diff -- $name"
}

case ${1:-} in
  check) check "${2:-}" ;;
  merge) merge "${2:-}" ;;
  *) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
