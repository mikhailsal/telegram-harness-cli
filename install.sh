#!/usr/bin/env bash
set -euo pipefail

# Installs a per-user toolchain and builds the current main commit in a cache.
project_dir=$PWD
cache_root=${XDG_CACHE_HOME:-"$HOME/.cache"}/telegram-harness-cli
toolchain_root=${XDG_DATA_HOME:-"$HOME/.local/share"}/thc/toolchains
node_version=24.22.0
codex_version=0.166.0
repository=mikhailsal/telegram-harness-cli
work_dir=
tool_work_dir=

fail() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"; }
cleanup() {
  if [[ -n $work_dir && -d $work_dir ]]; then rm -rf -- "$work_dir"; fi
  if [[ -n $tool_work_dir && -d $tool_work_dir ]]; then rm -rf -- "$tool_work_dir"; fi
}
trap cleanup EXIT

check_prerequisites() {
  [[ $(uname -s) == Linux ]] || fail 'Only Linux is supported.'
  for command in curl tar sha256sum flock mktemp awk; do require "$command"; done
}

node_is_compatible() {
  command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 &&
    node -e 'const [a,b,c]=process.versions.node.split(".").map(Number); process.exit(a>24 || (a===24 && (b>22 || (b===22 && c>=0))) ? 0 : 1)' >/dev/null 2>&1
}

install_node() {
  local arch archive base
  case $(uname -m) in
    x86_64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) fail "Unsupported architecture: $(uname -m)" ;;
  esac
  archive=node-v${node_version}-linux-${arch}.tar.gz
  base=https://nodejs.org/dist/v${node_version}
  if [[ ! -x $toolchain_root/node-v${node_version}/bin/node ]]; then
    curl -fsSL --connect-timeout 15 --max-time 300 "$base/SHASUMS256.txt" -o "$work_dir/SHASUMS256.txt"
    curl -fsSL --connect-timeout 15 --max-time 300 "$base/$archive" -o "$work_dir/$archive"
    awk -v file="$archive" '$2 == file { print }' "$work_dir/SHASUMS256.txt" > "$work_dir/checksum"
    [[ -s $work_dir/checksum ]] || fail 'Node checksum was not found.'
    (cd "$work_dir" && sha256sum -c checksum)
    mkdir "$tool_work_dir/node"
    tar -xzf "$work_dir/$archive" -C "$tool_work_dir/node" --strip-components=1
    mv "$tool_work_dir/node" "$toolchain_root/node-v${node_version}"
  fi
  PATH="$toolchain_root/node-v${node_version}/bin:$PATH"
  export PATH
}

install_codex() {
  if ! command -v codex >/dev/null 2>&1; then
    if [[ ! -x $toolchain_root/codex-${codex_version}/bin/codex ]]; then
      mkdir "$tool_work_dir/codex"
      npm install --global --prefix "$tool_work_dir/codex" "@openai/codex@$codex_version"
      mv "$tool_work_dir/codex" "$toolchain_root/codex-${codex_version}"
    fi
    PATH="$toolchain_root/codex-${codex_version}/bin:$PATH"
    export PATH
  fi
}

resolve_commit() {
  curl -fsSL --connect-timeout 15 --max-time 300 "https://api.github.com/repos/$repository/commits/main" -o "$work_dir/commit.json"
  node -e 'const c=require(process.argv[1]); const s=c.sha; const t=Date.parse(c.commit?.committer?.date); if (!/^[a-f0-9]{40}$/.test(s) || !Number.isFinite(t)) process.exit(1); console.log(s, Math.floor(t/1000))' "$work_dir/commit.json"
}

build_source() {
  local sha=$1 timestamp=$2 destination=$cache_root/source-$1
  if [[ ! -f $destination/dist/cli/main.js ]]; then
    curl -fsSL --connect-timeout 15 --max-time 300 "https://codeload.github.com/$repository/tar.gz/$sha" -o "$work_dir/source.tar.gz"
    mkdir "$work_dir/source"
    tar -xzf "$work_dir/source.tar.gz" -C "$work_dir/source" --strip-components=1
    node - "$work_dir/source" "0.0.0-source.$timestamp.g${sha:0:12}" <<'NODE'
const fs = require('node:fs');
const path = require('node:path');
const [dir, version] = process.argv.slice(2);
for (const name of ['package.json', 'package-lock.json']) {
  const file = path.join(dir, name);
  const data = JSON.parse(fs.readFileSync(file, 'utf8'));
  data.version = version;
  if (data.packages?.['']) data.packages[''].version = version;
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
}
NODE
    # The build needs TypeScript, a development dependency. Without the flag a
    # production-style environment (NODE_ENV=production, npm omit=dev) skips it.
    (cd "$work_dir/source" && npm ci --include=dev && npm run build)
    [[ -f $work_dir/source/dist/cli/main.js ]] || fail 'Build did not produce the CLI.'
    [[ ! -e $destination ]] || fail "Incomplete cache exists: $destination"
    mv "$work_dir/source" "$destination"
  fi
}

main() {
  local sha timestamp commit
  check_prerequisites
  mkdir -p "$cache_root" "$toolchain_root"
  exec 9>"$toolchain_root/bootstrap.lock"
  flock -w 120 9 || fail 'Timed out waiting for another bootstrap.'
  work_dir=$(mktemp -d "$cache_root/.bootstrap.XXXXXXXX")
  tool_work_dir=$(mktemp -d "$toolchain_root/.bootstrap.XXXXXXXX")
  printf 'bootstrap: preparing toolchain...\n' >&2
  if ! node_is_compatible; then install_node; fi
  install_codex
  printf 'bootstrap: resolving current source...\n' >&2
  commit=$(resolve_commit) || fail 'Could not resolve the main commit.'
  read -r sha timestamp <<< "$commit"
  [[ -n ${sha:-} && -n ${timestamp:-} ]] || fail 'Could not resolve the main commit.'
  printf 'bootstrap: preparing source %s...\n' "$sha" >&2
  build_source "$sha" "$timestamp"
  cleanup
  work_dir=
  tool_work_dir=
  flock -u 9
  if { : </dev/tty; } 2>/dev/null; then
    node "$cache_root/source-$sha/dist/cli/main.js" init --dir "$project_dir" "$@" </dev/tty
  else
    node "$cache_root/source-$sha/dist/cli/main.js" init --dir "$project_dir" "$@" </dev/null
  fi
}
main "$@"
