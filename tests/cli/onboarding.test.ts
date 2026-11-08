import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { PassThrough, Writable } from "node:stream";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import {
  interactiveToken,
  persistToken,
  prompt,
  requiredInput,
  verifyCodex,
} from "../../src/cli/onboarding.js";
import { configSchema } from "../../src/core/config.js";
import type { CliDependencies } from "../../src/cli/context.js";

const roots: string[] = [];
afterEach(async () => {
  for (const root of roots.splice(0))
    await rm(root, { recursive: true, force: true });
});

it("verifies the bot, stores the token privately and safely retries interrupted setup", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-onboarding-"));
  roots.push(root);
  let output = "";
  const deps = {
    home: root,
    stderr: (text: string) => {
      output += text;
    },
  } as CliDependencies;
  const io = {
    readToken: () => Promise.resolve("123456:abcdef"),
    getBot: () => Promise.resolve({ id: 123456, username: "test_bot" }),
  };
  const { path, token } = await interactiveToken(root, deps, io);
  await persistToken(path, token);
  expect(path).toContain(join(".config", "thc", "tokens"));
  expect((await stat(path)).mode & 0o777).toBe(0o600);
  expect(await readFile(path, "utf8")).toBe("123456:abcdef\n");
  expect(output).toContain("@test_bot");
  expect(output).not.toContain("123456:abcdef");
  expect((await interactiveToken(root, deps, io)).path).toBe(path);
  await persistToken(path, "987654:other");
  expect(await readFile(path, "utf8")).toBe("987654:other\n");
});
it("reports invalid or unverifiable tokens without exposing them", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-onboarding-"));
  roots.push(root);
  const deps = {
    home: root,
    stderr: () => {
      /* Ignore diagnostic output. */
    },
  } as unknown as CliDependencies;
  await expect(
    interactiveToken(root, deps, {
      readToken: () => Promise.resolve("bad"),
      getBot: () => Promise.reject(new Error("unused")),
    }),
  ).rejects.toThrow("Invalid bot token");
  await expect(
    interactiveToken(root, deps, {
      readToken: () => Promise.resolve("123:abc"),
      getBot: () => Promise.reject(new Error("secret")),
    }),
  ).rejects.toThrow("Could not verify");
  await expect(
    requiredInput(undefined, "sandbox", { isTTY: false } as CliDependencies),
  ).rejects.toThrow("--sandbox");
});

it("hides secret input and restores terminal mode on submission and cancellation", async () => {
  for (const [data, cancelled] of [
    ["123:secret\n", false],
    ["\u0003", true],
    ["\u0004", true],
  ] as const) {
    const input = new PassThrough() as PassThrough & {
      isTTY: boolean;
      setRawMode: (enabled: boolean) => void;
    };
    input.isTTY = true;
    const modes: boolean[] = [];
    input.setRawMode = (enabled) => {
      modes.push(enabled);
    };
    let output = "";
    const display = new Writable({
      write(chunk: Buffer, _encoding, done) {
        output += chunk.toString();
        done();
      },
    });
    const answer = prompt("Token: ", true, input, display);
    input.write(data);
    if (cancelled) await expect(answer).rejects.toThrow("Input cancelled");
    else expect(await answer).toBe("123:secret");
    expect(output).not.toContain("secret");
    expect(modes).toEqual([true, false]);
  }
});

it("uses explicit answers and rejects an empty interactive answer", async () => {
  const tty = { isTTY: true } as CliDependencies;
  const ask = () => Promise.resolve("workspace-write");
  expect(
    await requiredInput("read-only", "sandbox", tty, "Sandbox: ", ask),
  ).toBe("read-only");
  expect(await requiredInput(undefined, "sandbox", tty, "Sandbox: ", ask)).toBe(
    "workspace-write",
  );
  await expect(
    requiredInput(undefined, "sandbox", tty, "Sandbox: ", () =>
      Promise.resolve(""),
    ),
  ).rejects.toThrow("--sandbox");
});

const config = configSchema.parse({
  token: { env: "TOKEN" },
  allowlist: [1],
  sandbox: "read-only",
});
function authDeps(fail: (args: string[], call: number) => boolean, tty = true) {
  const calls: { args: string[]; inherit: boolean | undefined }[] = [];
  const deps = {
    env: {},
    isTTY: tty,
    exec: (_file: string, args: string[], options?: { inherit?: boolean }) => {
      calls.push({ args, inherit: options?.inherit });
      return fail(args, calls.length)
        ? Promise.reject(new Error("failed"))
        : Promise.resolve({ stdout: "", stderr: "" });
    },
  } as CliDependencies;
  return { deps, calls };
}
it("runs native login only after consent and verifies the resulting session", async () => {
  const { deps, calls } = authDeps(
    (args, call) => args.join(" ") === "login status" && call === 2,
  );
  await verifyCodex("/tmp", config, deps, () => Promise.resolve("yes"));
  expect(calls.map(({ args }) => args)).toEqual([
    ["--version"],
    ["login", "status"],
    ["login"],
    ["login", "status"],
  ]);
  expect(calls[2]?.inherit).toBe(true);
});
it("reports a missing Codex executable before considering login", async () => {
  const { deps, calls } = authDeps(() => true);
  await expect(verifyCodex("/tmp", config, deps)).rejects.toThrow(
    "Codex is unavailable",
  );
  expect(calls).toHaveLength(1);
});
it("does not invoke login in headless mode or when consent is declined", async () => {
  for (const tty of [false, true]) {
    const { deps, calls } = authDeps((args) => args[0] === "login", tty);
    await expect(
      verifyCodex("/tmp", config, deps, () => Promise.resolve("no")),
    ).rejects.toThrow("not signed in");
    expect(calls.map(({ args }) => args)).toEqual([
      ["--version"],
      ["login", "status"],
    ]);
  }
});
it("reports cancelled prompts and unsuccessful login or re-verification", async () => {
  const cancelled = authDeps((args) => args[0] === "login");
  await expect(
    verifyCodex("/tmp", config, cancelled.deps, () =>
      Promise.reject(new Error("cancelled")),
    ),
  ).rejects.toThrow("cancelled");
  for (const failure of [3, 4]) {
    const { deps } = authDeps((args, call) => call === 2 || call === failure);
    await expect(
      verifyCodex("/tmp", config, deps, () => Promise.resolve("y")),
    ).rejects.toThrow("login did not complete");
  }
});
