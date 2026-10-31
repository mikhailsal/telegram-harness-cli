import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const runner = resolve("tools/quality/launcher/scripts/shellcheck-revision.py");
let repository = "";

function git(...args: string[]): string {
  return execFileSync("git", args, {
    cwd: repository,
    encoding: "utf8",
  }).trim();
}

function initialize(): void {
  repository = mkdtempSync(join(tmpdir(), "thc-shellcheck-test-"));
  git("init", "-q");
  git("config", "user.name", "Test");
  git("config", "user.email", "test@example.invalid");
}

function commitScript(name: string, contents: string): string {
  writeFileSync(join(repository, name), contents);
  git("add", "--", name);
  git("commit", "-qm", "Add script");
  return git("rev-parse", "HEAD");
}

function check(revision: string): number | null {
  return spawnSync("python3", [runner, revision], { cwd: repository }).status;
}

afterEach(() => {
  if (repository) rmSync(repository, { recursive: true, force: true });
  repository = "";
});

describe("ShellCheck revision runner", () => {
  it("checks committed script bytes even if the worktree differs", () => {
    initialize();
    const revision = commitScript("script.sh", '#!/bin/sh\necho "$VALUE"\n');
    writeFileSync(join(repository, "script.sh"), "#!/bin/sh\necho $VALUE\n");
    expect(check(revision)).toBe(0);
  });

  it("checks a valid script whose name starts with a dash", () => {
    initialize();
    const revision = commitScript("-leading.sh", '#!/bin/sh\necho "$VALUE"\n');
    expect(check(revision)).toBe(0);
  });

  it("rejects a bad committed script with spaces in its path", () => {
    initialize();
    const revision = commitScript("bad script.sh", "#!/bin/sh\necho $VALUE\n");
    writeFileSync(
      join(repository, "bad script.sh"),
      '#!/bin/sh\necho "$VALUE"\n',
    );
    expect(check(revision)).toBe(1);
  });
});
