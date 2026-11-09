import {
  mkdtemp,
  mkdir,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { CliError } from "../../src/cli/errors.js";
import { withBotBinding } from "../../src/cli/bot-binding.js";
import { withSetupLock } from "../../src/cli/setup-lock.js";
import { acquireDirectoryLock } from "../../src/core/lock.js";
import { Store } from "../../src/store/index.js";

const token = "12345:secret_token";
const roots: string[] = [];
afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});
async function fixture() {
  const home = await mkdtemp(join(tmpdir(), "thc-binding-"));
  roots.push(home);
  const a = join(home, "a");
  const b = join(home, "b");
  await Promise.all([mkdir(a), mkdir(b)]);
  return { home, a, b };
}
async function configure(directory: string, value: unknown = {}) {
  await mkdir(join(directory, ".thc"), { recursive: true });
  await writeFile(
    join(directory, ".thc", "config.json"),
    JSON.stringify(value),
  );
}
/** Persist the identity a bridge records once it has talked to its bot. */
function identify(directory: string, botId: string) {
  const store = new Store(join(directory, ".thc", "store.db"));
  store.putEntity({
    id: "entity",
    directory,
    botId,
    createdAt: "2026-01-01",
  });
  store.setCursor("entity-id", "entity");
  store.close();
}
function deferred() {
  let done!: () => void;
  const promise = new Promise<void>((resolve) => {
    done = resolve;
  });
  return { promise, done };
}

it("serializes a bot and rejects a second configured project, allowing aliases", async () => {
  const { home, a, b } = await fixture();
  const entered = deferred();
  const release = deferred();
  const first = withBotBinding(home, a, token, {}, async () => {
    entered.done();
    await release.promise;
    await configure(a);
  });
  await entered.promise;
  const second = withBotBinding(home, b, token, {}, () => Promise.resolve());
  release.done();
  await first;
  await expect(second).rejects.toThrow(a);
  const alias = join(home, "alias");
  await symlink(a, alias);
  await expect(
    withBotBinding(home, alias, token, {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
  const record = await readFile(
    join(home, ".local/share/thc/bots/12345.json"),
    "utf8",
  );
  expect(record).not.toContain("secret_token");
});
it("allows distinct bots to run concurrently and releases failed reservations", async () => {
  const { home, a, b } = await fixture();
  const entered = deferred();
  const release = deferred();
  const first = withBotBinding(home, a, token, {}, async () => {
    entered.done();
    await release.promise;
    throw new Error("aborted");
  });
  await entered.promise;
  await expect(
    withBotBinding(home, b, "987:other", {}, () => Promise.resolve(42)),
  ).resolves.toBe(42);
  release.done();
  await expect(first).rejects.toThrow("aborted");
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
});
it("retains a failed setup that wrote config and reclaims missing projects", async () => {
  const { home, a, b } = await fixture();
  await expect(
    withBotBinding(home, a, token, {}, async () => {
      await configure(a);
      throw new Error("later failure");
    }),
  ).rejects.toThrow("later failure");
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve()),
  ).rejects.toThrow(a);
  const missing = join(home, "gone");
  await writeFile(
    join(home, ".local/share/thc/bots/12345.json"),
    JSON.stringify({ directory: missing }),
  );
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
});
it("keeps a claim while its bridge still runs after the configuration was removed", async () => {
  const { home, a, b } = await fixture();
  await withBotBinding(home, a, token, {}, () => configure(a));
  await rm(join(a, ".thc", "config.json"));
  const bridge = await acquireDirectoryLock(a);
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve()),
  ).rejects.toThrow(a);
  await bridge.release();
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
});
it("refuses to reclaim a claim whose running state cannot be read", async () => {
  const { home, a, b } = await fixture();
  await withBotBinding(home, a, token, {}, () => configure(a));
  await rm(join(a, ".thc", "config.json"));
  await mkdir(join(a, ".thc", "lock-owner.json"));
  await expect(
    withBotBinding(home, b, token, {}, () => Promise.resolve()),
  ).rejects.toMatchObject({
    info: {
      code: "E_CONFIG",
      what: `Cannot identify the bot configured for ${a}`,
    },
  });
});
it("does not trust a caller environment to replace a binding", async () => {
  const { home, a, b } = await fixture();
  await withBotBinding(home, a, token, {}, async () => {
    await configure(a, { token: { env: "BOT" } });
  });
  await expect(
    withBotBinding(home, b, token, { BOT: "999:other" }, () =>
      Promise.resolve(),
    ),
  ).rejects.toThrow(a);
});
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
it("times out and releases the setup lease after errors", async () => {
  const { home } = await fixture();
  const entered = deferred();
  const release = deferred();
  const first = withSetupLock(home, "shared", async () => {
    entered.done();
    await release.promise;
  });
  await entered.promise;
  await expect(
    withSetupLock(home, "shared", () => Promise.resolve(), {
      timeoutMs: 20,
      retryMs: 5,
    }),
  ).rejects.toThrow("Timed out");
  release.done();
  await first;
  await expect(
    withSetupLock(home, "shared", () => Promise.reject(new Error("fail"))),
  ).rejects.toThrow("fail");
  await expect(
    withSetupLock(home, "shared", () => Promise.resolve(1)),
  ).resolves.toBe(1);
});

it.each(["{", "{}"])(
  "rejects malformed binding metadata %s",
  async (record) => {
    const { home, a } = await fixture();
    const directory = join(home, ".local/share/thc/bots");
    await mkdir(directory, { recursive: true });
    const path = join(directory, "12345.json");
    await writeFile(path, record);
    const error = await withBotBinding(home, a, token, {}, () =>
      Promise.resolve(),
    ).catch((value: unknown) => value);
    expect(error).toBeInstanceOf(CliError);
    if (!(error instanceof CliError)) throw new Error("Expected CliError");
    expect(error.info.code).toBe("E_CONFIG");
    expect(error.info.fix).toContain(path);
    expect(await readFile(path, "utf8")).toBe(record);
  },
);

it("rejects invalid tokens without writing metadata or exposing the token", async () => {
  const { home, a } = await fixture();
  const invalid = "12345:secret token";
  const error = await withBotBinding(home, a, invalid, {}, () =>
    Promise.resolve(),
  ).catch((value: unknown) => value);
  expect(error).toMatchObject({ info: { code: "E_CONFIG" } });
  expect(String(error)).not.toContain(invalid);
});

it("reports an unreadable registered configuration with its project path", async () => {
  const { home, a, b } = await fixture();
  await mkdir(join(a, ".thc", "config.json"), { recursive: true });
  const root = join(home, ".local/share/thc");
  await mkdir(root, { recursive: true });
  await writeFile(join(root, "folders"), JSON.stringify({ [a]: "1.0.0" }));
  const error = await withBotBinding(home, b, token, {}, () =>
    Promise.resolve(),
  ).catch((value: unknown) => value);
  expect(error).toBeInstanceOf(CliError);
  if (!(error instanceof CliError)) throw new Error("Expected CliError");
  expect(error.info.code).toBe("E_CONFIG");
  expect(error.info.fix).toContain(a);
});

it("lets the folder that holds the claim rerun despite an unrelated unidentifiable folder", async () => {
  const { home, a, b } = await fixture();
  await withBotBinding(home, a, token, {}, () => configure(a));
  await configure(b, { token: { env: "MISSING" } });
  const root = join(home, ".local/share/thc");
  await writeFile(join(root, "folders"), JSON.stringify({ [b]: "1.0.0" }));
  await expect(
    withBotBinding(home, a, token, {}, () => Promise.resolve(1)),
  ).resolves.toBe(1);
  const c = join(home, "c");
  await mkdir(c);
  const error = await withBotBinding(home, c, "999:other", {}, () =>
    Promise.resolve(),
  ).catch((value: unknown) => value);
  expect(error).toBeInstanceOf(CliError);
  if (!(error instanceof CliError)) throw new Error("Expected CliError");
  expect(error.info.fix).toContain(b);
});
