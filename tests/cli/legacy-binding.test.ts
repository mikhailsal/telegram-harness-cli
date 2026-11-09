import { mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { withBotBinding } from "../../src/cli/bot-binding.js";
import { acquireDirectoryLock } from "../../src/core/lock.js";
import { Store } from "../../src/store/index.js";
import { migrate } from "../../src/store/schema.js";
import {
  configure,
  fixture,
  identify,
  removeFixtures,
  token,
} from "./binding-fixtures.js";

afterEach(removeFixtures);
it("uses persisted bot identity instead of the caller environment", async () => {
  const { home, a, b } = await fixture();
  await configure(a, { token: { env: "BOT" } });
  const store = new Store(join(a, ".thc", "store.db"));
  store.putEntity({
    id: "entity",
    directory: a,
    botId: "12345",
    createdAt: "2026-01-01",
  });
  store.setCursor("entity-id", "entity");
  store.close();
  const root = join(home, ".local/share/thc");
  await mkdir(root, { recursive: true });
  await writeFile(join(root, "folders"), JSON.stringify({ [a]: "1.0.0" }));
  await expect(
    withBotBinding(home, b, token, { BOT: "999:other" }, () =>
      Promise.resolve(),
    ),
  ).rejects.toThrow(a);
  await expect(
    withBotBinding(home, b, "999:other", {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
});
it("keeps the bot of a registered legacy project whose bridge runs without its configuration", async () => {
  const { home, a, b } = await fixture();
  await mkdir(join(a, ".thc"), { recursive: true });
  identify(a, "12345");
  const root = join(home, ".local/share/thc");
  await mkdir(root, { recursive: true });
  await writeFile(join(root, "folders"), JSON.stringify({ [a]: "1.0.0" }));
  const bridge = await acquireDirectoryLock(a);
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve()),
  ).rejects.toThrow(a);
  await expect(
    withBotBinding(home, b, "999:other", {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
  await bridge.release();
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
});
/** A legacy folder is registered, and its bridge was started, by its symlink. */
async function aliased(home: string, directory: string) {
  const alias = join(home, "alias");
  await symlink(directory, alias);
  const root = join(home, ".local/share/thc");
  await mkdir(root, { recursive: true });
  await writeFile(join(root, "folders"), JSON.stringify({ [alias]: "1.0.0" }));
  return alias;
}
it("keeps the bot of a legacy project whose bridge was started through a symlink", async () => {
  const { home, a, b } = await fixture();
  await mkdir(join(a, ".thc"), { recursive: true });
  identify(a, "12345");
  const bridge = await acquireDirectoryLock(await aliased(home, a));
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve()),
  ).rejects.toMatchObject({
    info: { what: `This bot is already configured for ${a}` },
  });
  await bridge.release();
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
});
it("keeps a claim while a bridge started through a symlink still runs", async () => {
  const { home, a, b } = await fixture();
  await withBotBinding(home, a, token, {}, () => configure(a));
  await rm(join(a, ".thc", "config.json"));
  const bridge = await acquireDirectoryLock(await aliased(home, a));
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve()),
  ).rejects.toMatchObject({
    info: { what: `This bot is already configured for ${a}` },
  });
  await bridge.release();
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
});
it("identifies the bot of a running legacy project whose store keeps an older schema", async () => {
  const { home, a, b } = await fixture();
  await mkdir(join(a, ".thc"), { recursive: true });
  const db = new DatabaseSync(join(a, ".thc", "store.db"));
  migrate(db, 1);
  db.prepare("INSERT INTO entities VALUES (?, ?, ?, ?)").run(
    "entity",
    a,
    "12345",
    "2026-01-01",
  );
  db.exec("INSERT INTO meta VALUES ('entity-id', 'entity')");
  db.close();
  const root = join(home, ".local/share/thc");
  await mkdir(root, { recursive: true });
  await writeFile(join(root, "folders"), JSON.stringify({ [a]: "1.0.0" }));
  const bridge = await acquireDirectoryLock(a);
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve()),
  ).rejects.toMatchObject({
    info: { what: `This bot is already configured for ${a}` },
  });
  await expect(
    withBotBinding(home, b, "999:other", {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
  await bridge.release();
});
it("refuses to guess the bot of a running legacy project without a readable identity", async () => {
  const { home, a, b } = await fixture();
  const root = join(home, ".local/share/thc");
  await mkdir(root, { recursive: true });
  await writeFile(join(root, "folders"), JSON.stringify({ [a]: "1.0.0" }));
  const bridge = await acquireDirectoryLock(a);
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve()),
  ).rejects.toMatchObject({
    info: {
      code: "E_CONFIG",
      what: `Cannot identify the bot configured for ${a}`,
    },
  });
  await bridge.release();
});
it.each(["missing", "malformed"])(
  "reports an unidentifiable legacy %s configuration",
  async (kind) => {
    const { home, a, b } = await fixture();
    await configure(a, { token: { env: "MISSING" } });
    if (kind === "malformed")
      await writeFile(join(a, ".thc", "config.json"), "{");
    const root = join(home, ".local/share/thc");
    await mkdir(root, { recursive: true });
    await writeFile(
      join(root, "folders"),
      JSON.stringify({ [a]: "1.0.0", [join(home, "deleted")]: "1.0.0" }),
    );
    const error = await withBotBinding(home, b, token, {}, () =>
      Promise.resolve(),
    ).catch((e: unknown) => e);
    expect(String(error)).toContain(a);
    expect(error).toMatchObject({ info: { code: "E_CONFIG" } });
    expect(String(error)).not.toContain(token);
  },
);
it.each(["file", "env"])(
  "finds legacy %s tokens without exposing them",
  async (kind) => {
    const { home, a, b } = await fixture();
    await writeFile(join(a, "token"), token, { mode: 0o600 });
    await configure(a, {
      token: kind === "file" ? { file: "token" } : { env: "BOT" },
    });
    const root = join(home, ".local/share/thc");
    await mkdir(root, { recursive: true });
    await writeFile(join(root, "folders"), JSON.stringify({ [a]: "1.0.0" }));
    const error = await withBotBinding(home, b, token, { BOT: token }, () =>
      Promise.resolve(),
    ).catch((e: unknown) => e);
    expect(String(error)).toContain(a);
    expect(String(error)).not.toContain(token);
  },
);
