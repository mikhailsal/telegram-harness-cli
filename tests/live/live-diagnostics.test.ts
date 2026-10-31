import { mkdtemp, mkdir, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { expect, it } from "vitest";
import { saveFailure } from "./live-diagnostics.ts";
import { TmuxRunner } from "../../src/adapters/codex-tmux/runner.ts";
import type { CodexTmuxAdapter } from "../../src/adapters/codex-tmux/index.ts";
import { vi } from "vitest";

it("retains a private consistent owned database and synthetic failure context", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-failure-fixture-"));
  await mkdir(join(root, ".thc"));
  const db = new DatabaseSync(join(root, ".thc", "store.db"));
  db.exec(
    `CREATE TABLE meta(name TEXT, value TEXT); CREATE TABLE inbox(updateId INTEGER, turnId TEXT); CREATE TABLE turns(id TEXT, status TEXT, createdAt TEXT); CREATE TABLE messages(author TEXT, turnId TEXT, text TEXT); CREATE TABLE pending_requests(id TEXT, createdAt TEXT); CREATE TABLE transient(id TEXT, createdAt TEXT); INSERT INTO meta VALUES('marker', 'synthetic');`,
  );
  try {
    const target = await saveFailure(
      { root, owned: new Map(), started: false },
      new Error("fixture failure"),
      join(root, "failures"),
    );
    expect((await stat(target)).mode & 0o777).toBe(0o700);
    expect((await stat(join(target, "store.db"))).mode & 0o777).toBe(0o600);
    expect(await readFile(join(target, "error.log"), "utf8")).toContain(
      "fixture failure",
    );
    expect(await readFile(join(target, "snapshot.json"), "utf8")).toContain(
      "synthetic",
    );
    const saved = new DatabaseSync(join(target, "store.db"), {
      readOnly: true,
    });
    try {
      expect(saved.prepare("SELECT value FROM meta").get()?.value).toBe(
        "synthetic",
      );
    } finally {
      saved.close();
    }
  } finally {
    db.close();
    await rm(root, { recursive: true, force: true });
  }
});
it("captures only an identity-checked owned pane", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-failure-pane-"));
  const name = "thc-12345678-1234-1234-1234-123456789abc";
  const identity = {
    paneId: "%1",
    pid: "1",
    executable: "/codex",
    startTime: "1",
  };
  const adapter = {
    snapshot: () => ({ cwd: root, identity }),
  } as CodexTmuxAdapter;
  const guard = vi
    .spyOn(TmuxRunner.prototype, "guardWorkspace")
    .mockResolvedValue();
  vi.spyOn(TmuxRunner.prototype, "capture").mockResolvedValue("synthetic pane");
  try {
    const target = await saveFailure(
      { root, owned: new Map([[name, adapter]]), started: false },
      new Error("failure"),
      join(root, "failures"),
    );
    expect(guard).toHaveBeenCalledWith(identity, root);
    expect(await readFile(join(target, `${name}.pane.txt`), "utf8")).toBe(
      "synthetic pane",
    );
  } finally {
    vi.restoreAllMocks();
    await rm(root, { recursive: true, force: true });
  }
});
