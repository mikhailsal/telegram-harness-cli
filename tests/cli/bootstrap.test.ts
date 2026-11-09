import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";

const script = resolve("install.sh");
const sha = "abcdef0123456789abcdef0123456789abcdef01";
let root: string;
let bin: string;
let home: string;
let project: string;

function executable(name: string, body: string): void {
  writeFileSync(join(bin, name), `#!/bin/bash\nset -e\n${body}\n`, {
    mode: 0o755,
  });
}
function run(
  input?: string,
  args: string[] = [],
  extraEnv: Record<string, string> = {},
) {
  return spawnSync(
    "bash",
    input === undefined ? [script, ...args] : ["-s", "--", ...args],
    {
      cwd: project,
      env: {
        ...process.env,
        HOME: home,
        XDG_CACHE_HOME: join(home, "cache"),
        XDG_DATA_HOME: join(home, ".local", "share"),
        PATH: `${bin}:/usr/bin:/bin`,
        FIXTURE: root,
        ...extraEnv,
      },
      input,
      encoding: "utf8",
    },
  );
}
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "bootstrap test "));
  bin = join(root, "bin");
  home = join(root, "home");
  project = join(root, "project with spaces");
  for (const dir of [bin, home, project, join(root, "source", "repo")])
    mkdirSync(dir, { recursive: true });
  const source = join(root, "source", "repo");
  writeFileSync(join(source, "package.json"), '{"version":"0.0.0"}');
  writeFileSync(
    join(source, "package-lock.json"),
    '{"version":"0.0.0","packages":{"":{"version":"0.0.0"}}}',
  );
  execFileSync("tar", [
    "-czf",
    join(root, "source.tar.gz"),
    "-C",
    join(root, "source"),
    "repo",
  ]);
  executable(
    "node",
    `if [[ "$1" == -e && "$2" == *process.versions.node* ]]; then exit 0; fi\nexec '${process.execPath}' "$@"`,
  );
  executable("codex", "exit 0");
  executable(
    "curl",
    `
url=$6
out=$8
printf '%s\\n' "$url" >> "$FIXTURE/downloads"
case "$url" in
  *api.github.com*) printf '%s' '{"sha":"${sha}","commit":{"committer":{"date":"2026-01-01T00:00:00Z"}}}' > "$out" ;;
  *codeload.github.com*) cp "$FIXTURE/source.tar.gz" "$out" ;;
  *) exit 1 ;;
esac`,
  );
  executable(
    "npm",
    `
printf '%s\\n' "$*" >> "$FIXTURE/npm.log"
if [[ "$*" == 'run build' ]]; then
  mkdir -p dist/cli
  cat > dist/cli/main.js <<'JS'
console.log(JSON.stringify({args: process.argv.slice(2), cwd: process.cwd(), stdin: require('node:fs').readFileSync(0, 'utf8')}));
JS
fi`,
  );
});
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

it("builds once, preserves the project, and does not pass the piped script as input", () => {
  const first = run(readFileSync(script, "utf8"));
  expect(first.status, first.stderr).toBe(0);
  const result: unknown = JSON.parse(first.stdout.trim());
  expect(result).toEqual({
    args: ["init", "--dir", project],
    cwd: project,
    stdin: "",
  });
  const cached = join(home, "cache", "telegram-harness-cli", `source-${sha}`);
  const pkg = JSON.parse(
    readFileSync(join(cached, "package.json"), "utf8"),
  ) as { version: string };
  const lock = JSON.parse(
    readFileSync(join(cached, "package-lock.json"), "utf8"),
  ) as { version: string; packages: Record<string, { version: string }> };
  expect(pkg.version).toBe("0.0.0-source.1767225600.gabcdef012345");
  expect(lock.version).toBe(pkg.version);
  const rootPackage = lock.packages[""];
  if (!rootPackage) throw new Error("Missing lockfile root package");
  expect(rootPackage.version).toBe(pkg.version);
  const second = run(undefined, ["--no-start"]);
  expect(second.status).toBe(0);
  expect(second.stdout).toContain("--no-start");
  expect(
    readFileSync(join(root, "npm.log"), "utf8").trim().split("\n"),
  ).toEqual(["ci --include=dev", "run build"]);
  expect(
    readFileSync(join(root, "downloads"), "utf8").match(/codeload/g),
  ).toHaveLength(1);
});

it("stops before executing the CLI if the source download fails", () => {
  executable(
    "curl",
    `if [[ "$6" == *codeload* ]]; then exit 22; fi\nprintf '%s' '{"sha":"${sha}","commit":{"committer":{"date":"2026-01-01T00:00:00Z"}}}' > "$8"`,
  );
  const result = run();
  expect(result.status).not.toBe(0);
  expect(result.stdout).not.toContain('"args"');
});

it("installs development dependencies for the build when the environment omits them", () => {
  executable(
    "npm",
    `
printf '%s\\n' "$*" >> "$FIXTURE/npm.log"
case "$1" in
  ci)
    mkdir -p node_modules
    # Like npm: NODE_ENV=production skips dev dependencies unless they are requested.
    if [[ "\${NODE_ENV:-}" != production || "$*" == *--include=dev* ]]; then
      mkdir -p node_modules/typescript/bin && : > node_modules/typescript/bin/tsc
    fi ;;
  run)
    [[ -e node_modules/typescript/bin/tsc ]] || { echo 'build: typescript is not installed' >&2; exit 1; }
    mkdir -p dist/cli
    echo "console.log(JSON.stringify({args: process.argv.slice(2)}));" > dist/cli/main.js ;;
esac`,
  );
  const result = run(undefined, [], { NODE_ENV: "production" });
  expect(result.status, result.stderr).toBe(0);
  expect(result.stdout).toContain('"args"');
});

it("reports missing prerequisites before making changes", () => {
  const isolated = join(root, "isolated");
  mkdirSync(isolated);
  symlinkSync("/usr/bin/uname", join(isolated, "uname"));
  const result = spawnSync("/bin/bash", [script], {
    cwd: project,
    env: { HOME: home, PATH: isolated },
    encoding: "utf8",
  });
  expect(result.status).not.toBe(0);
  expect(result.stderr).toContain("Missing required command: curl");
});

it("keeps existing project files and the caller's Codex installation untouched", () => {
  mkdirSync(join(project, ".thc"));
  writeFileSync(join(project, ".thc", "version"), "existing-version\n");
  expect(run().status).toBe(0);
  expect(readFileSync(join(project, ".thc", "version"), "utf8")).toBe(
    "existing-version\n",
  );
  expect(readFileSync(join(root, "npm.log"), "utf8")).not.toContain(
    "@openai/codex",
  );
});

function preparePrivateNode(): { archive: string; checksum: string } {
  const release = `node-v24.22.0-linux-${process.arch}`;
  const nodeDir = join(root, "node-release", release, "bin");
  mkdirSync(nodeDir, { recursive: true });
  copyFileSync(join(bin, "node"), join(nodeDir, "node"));
  writeFileSync(
    join(nodeDir, "npm"),
    `#!/bin/bash
set -e
if [[ "$1" == install && "$2" == --global && "$3" == --prefix && "$5" == @openai/codex@0.166.0 ]]; then
  mkdir -p "$4/bin"
  printf '#!/bin/bash\nexit 0\n' > "$4/bin/codex"
  chmod +x "$4/bin/codex"
else
  exec "$FIXTURE/bin/npm" "$@"
fi
`,
    { mode: 0o755 },
  );
  rmSync(join(bin, "node"));
  rmSync(join(bin, "codex"));
  const archive = `${release}.tar.gz`;
  execFileSync("tar", [
    "-czf",
    join(root, archive),
    "-C",
    join(root, "node-release"),
    release,
  ]);
  const checksum = createHash("sha256")
    .update(readFileSync(join(root, archive)))
    .digest("hex");
  return { archive, checksum };
}

function mockNodeDownloads(
  archive: string,
  checksum: string,
  listed = archive,
): void {
  executable(
    "curl",
    `
url=$6
out=$8
case "$url" in
  *SHASUMS256.txt) printf '%s  %s\n' '${checksum}' '${listed}' > "$out" ;;
  *nodejs.org*) cp "$FIXTURE/${archive}" "$out" ;;
  *api.github.com*) printf '%s' '{"sha":"${sha}","commit":{"committer":{"date":"2026-01-01T00:00:00Z"}}}' > "$out" ;;
  *codeload.github.com*) cp "$FIXTURE/source.tar.gz" "$out" ;;
  *) exit 1 ;;
esac`,
  );
}

it("verifies and installs private Node and missing Codex", () => {
  const { archive, checksum } = preparePrivateNode();
  mockNodeDownloads(archive, checksum);
  const result = run();
  expect(result.status, result.stderr).toBe(0);
  expect(result.stdout).toContain(`${archive}: OK`);
  expect(result.stdout).toContain('"args"');
  expect(
    readFileSync(
      join(
        home,
        ".local",
        "share",
        "thc",
        "toolchains",
        "codex-0.166.0",
        "bin",
        "codex",
      ),
      "utf8",
    ),
  ).toContain("exit 0");
});

function refusedNode(wrongChecksum?: string, listedName?: string) {
  const { archive, checksum } = preparePrivateNode();
  mockNodeDownloads(archive, wrongChecksum ?? checksum, listedName);
  const result = run();
  expect(result.status).not.toBe(0);
  expect(result.stdout).not.toContain('"args"');
  const toolchains = join(home, ".local", "share", "thc", "toolchains");
  expect(existsSync(join(toolchains, "node-v24.22.0"))).toBe(false);
  return result;
}

it("refuses a private Node archive whose checksum does not match", () => {
  expect(refusedNode("0".repeat(64)).stdout).toContain("FAILED");
});

it("refuses a private Node archive that has no checksum entry", () => {
  expect(refusedNode(undefined, "node-v0.0.0-other.tar.gz").stderr).toContain(
    "bootstrap: Node checksum was not found.",
  );
});
