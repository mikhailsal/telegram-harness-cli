import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { run, type CliDependencies } from "../../src/cli/main.js";

const asked = vi.hoisted(() => [] as string[]);

vi.mock("../../src/cli/identity.js", () => ({
  respondWithPrivateIds: () => Promise.resolve(),
}));
vi.mock("../../src/cli/onboarding.js", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../../src/cli/onboarding.js")>();
  return {
    ...original,
    requiredInput: (
      value: string | undefined,
      name: string,
      ...rest: [unknown, string?]
    ) => {
      if (value) return Promise.resolve(value);
      asked.push(rest[1] ?? `${name}: `);
      if (name === "user-id") return Promise.resolve("123");
      return Promise.reject(new Error("stop after the user-id prompt"));
    },
  };
});

let directory: string;

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-user-id-prompt-"));
  asked.length = 0;
});
afterEach(() => {
  rmSync(directory, { recursive: true, force: true });
});

it("asks for the Telegram user ID with a readable label", async () => {
  const token = join(directory, "token");
  writeFileSync(token, "123456:abcdefghijklmnopqrstuvwxyz", { mode: 0o600 });
  let errors = "";
  const deps: Partial<CliDependencies> = {
    cwd: directory,
    home: join(directory, "home"),
    env: {},
    isTTY: true,
    stdout: () => undefined,
    stderr: (text) => {
      errors += text;
    },
  };
  await run(["init", "--token-file", token], deps);
  expect(errors).toContain("reply with your numeric user ID");
  expect(asked[0]).toBe("Your Telegram user ID: ");
});
