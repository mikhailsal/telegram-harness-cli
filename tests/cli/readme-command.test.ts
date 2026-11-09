import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";

let root: string;
let bin: string;
let scratch: string;
let project: string;

/** The single command the README tells people to paste into a terminal. */
function documentedCommand(): string {
  const readme = readFileSync(resolve("README.md"), "utf8");
  const section = readme.split("## Getting started")[1] ?? "";
  const command = /```sh\n([^\n]+)\n```/.exec(section)?.[1];
  if (!command) throw new Error("README has no one-line installer command");
  return command;
}
function fakeCurl(body: string): void {
  writeFileSync(
    join(bin, "curl"),
    `#!/bin/bash\nset -e\nprintf '%s\\n' "$*" > "$FIXTURE/curl-args"\nout=$4\n${body}\n`,
    { mode: 0o755 },
  );
}
function pasteCommand(input: string) {
  return spawnSync("bash", ["-c", documentedCommand()], {
    cwd: project,
    env: {
      PATH: `${bin}:/usr/bin:/bin`,
      HOME: root,
      TMPDIR: scratch,
      FIXTURE: root,
    },
    input,
    encoding: "utf8",
  });
}
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "thc-readme-"));
  bin = join(root, "bin");
  scratch = join(root, "scratch");
  project = join(root, "project with spaces");
  for (const dir of [bin, scratch, project]) mkdirSync(dir);
});
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

it("runs the downloaded installer in the current project with the caller's input, then cleans up", () => {
  fakeCurl(`cat > "$out" <<'SH'
IFS= read -r line
printf '%s\\n%s' "$PWD" "$line" > "$FIXTURE/observed"
SH`);
  const result = pasteCommand("typed by the user\n");
  expect(result.status, result.stderr).toBe(0);
  expect(readFileSync(join(root, "observed"), "utf8")).toBe(
    `${realpathSync(project)}\ntyped by the user`,
  );
  expect(readFileSync(join(root, "curl-args"), "utf8")).toMatch(
    /^-fsSL https:\/\/\S+\/install\.sh -o \S+\n$/,
  );
  expect(readdirSync(scratch)).toEqual([]);
});

it("does not run a partially downloaded installer and still cleans up", () => {
  fakeCurl(
    `printf 'printf partial > "$FIXTURE/observed"\\n' > "$out"\nexit 18`,
  );
  const result = pasteCommand("");
  expect(result.status).toBe(18);
  expect(existsSync(join(root, "observed"))).toBe(false);
  expect(readdirSync(scratch)).toEqual([]);
});
