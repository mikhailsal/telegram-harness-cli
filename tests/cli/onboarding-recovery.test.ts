import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, expect, it } from "vitest";
import { run, type CliDependencies } from "../../src/cli/main.js";
import { openStore, SCHEMA_VERSION } from "../../src/store/index.js";

let directory: string;
let output: string;
let errors: string;
let calls: string[];
let deps: Partial<CliDependencies>;

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-recovery-"));
  output = "";
  errors = "";
  calls = [];
  const pkg = join(directory, "package");
  for (const path of ["dist/cli", "node_modules", "skills/telegram-history"])
    mkdirSync(join(pkg, path), { recursive: true });
  writeFileSync(
    join(pkg, "package.json"),
    JSON.stringify({ version: "1.2.3" }),
  );
  writeFileSync(join(pkg, "package-lock.json"), "{}");
  writeFileSync(join(pkg, "dist", "cli", "main.js"), "export {};");
  writeFileSync(
    join(pkg, "skills", "telegram-history", "SKILL.md"),
    "# History\n",
  );
  writeFileSync(join(directory, "token"), "123456:abcdefghijklmnopqrstuvwxyz", {
    mode: 0o600,
  });
  deps = {
    cwd: directory,
    home: join(directory, "home"),
    packageRoot: pkg,
    env: {},
    isTTY: false,
    stdout: (text) => {
      output += text;
    },
    stderr: (text) => {
      errors += text;
    },
    exec: (file, args) => {
      calls.push([file, ...args].join(" "));
      return Promise.resolve({ stdout: "ok", stderr: "" });
    },
  };
});
afterEach(() => {
  rmSync(directory, { recursive: true, force: true });
});

function initArgs(...extra: string[]): string[] {
  return [
    "init",
    "--token-file",
    join(directory, "token"),
    "--user-id",
    "123",
    "--sandbox",
    "workspace-write",
    "--yes",
    ...extra,
  ];
}
function cursor(name: string, value?: string): string | undefined {
  const store = openStore(join(directory, ".thc", "store.db"));
  try {
    if (value !== undefined) store.setCursor(name, value);
    return store.getCursor(name);
  } finally {
    store.close();
  }
}

it("confirms the first answer on the rerun after a failed service start", async () => {
  let failing = true;
  deps.exec = (file, args) => {
    calls.push([file, ...args].join(" "));
    return failing && args.includes("--now")
      ? Promise.reject(new Error("systemd user bus unavailable"))
      : Promise.resolve({ stdout: "ok", stderr: "" });
  };
  expect(await run(initArgs(), deps)).not.toBe(0);
  expect(output).not.toContain("Bridge started");
  failing = false;
  let clock = Date.parse("2026-10-31T12:00:00.000Z");
  deps.now = () => clock;
  deps.sleep = (milliseconds) => {
    clock += milliseconds;
    cursor("first-delivery-at", new Date(clock).toISOString());
    return Promise.resolve();
  };
  expect(await run(initArgs(), deps)).toBe(0);
  expect(output).toContain("Bridge started. Send a message to the bot.");
  expect(output).toContain("Working: first answer in 1 s");
});

it("restarts a project that already answered without repeating the onboarding", async () => {
  expect(await run(initArgs("--no-start"), deps)).toBe(0);
  cursor("first-delivery-at", "2026-10-31T12:00:00.000Z");
  output = "";
  expect(await run(["init", "--update"], deps)).toBe(0);
  expect(calls.some((call) => call.startsWith("systemctl --user stop "))).toBe(
    true,
  );
  expect(calls.some((call) => call.includes(" enable --now "))).toBe(true);
  expect(output).not.toContain("Bridge started");
  expect(cursor("welcome-pending")).toBeFalsy();
});

it("leaves the store alone when the retained version uses an older schema", async () => {
  expect(await run(initArgs("--no-start"), deps)).toBe(0);
  const older = SCHEMA_VERSION - 1;
  const retained = join(directory, "home", ".local", "share", "thc", "1.0.0");
  mkdirSync(join(retained, "dist", "cli"), { recursive: true });
  writeFileSync(join(retained, "dist", "cli", "main.js"), "export {};");
  writeFileSync(join(retained, "package.json"), '{"version":"1.0.0"}');
  writeFileSync(
    join(retained, "version.json"),
    JSON.stringify({ version: "1.0.0", schema: older }),
  );
  writeFileSync(join(directory, ".thc", "version"), "1.0.0\n");
  cursor("retained-marker", "1"); // creates the store at the current schema
  const path = join(directory, ".thc", "store.db");
  const raw = new DatabaseSync(path);
  raw.exec(`PRAGMA user_version = ${String(older)}`);
  raw.close();
  output = "";
  calls = [];
  // The installer is newer than the retained runtime: opening the store here
  // would migrate it to a schema that runtime refuses to read.
  expect(await run(initArgs(), deps), errors).toBe(0);
  expect(calls.some((call) => call.includes(" enable --now "))).toBe(true);
  expect(output).not.toContain("Bridge started");
  const after = new DatabaseSync(path, { readOnly: true });
  const version = after.prepare("PRAGMA user_version").get() as {
    user_version: number;
  };
  after.close();
  expect(version.user_version).toBe(older);
});

it("rejects an explicit setting that contradicts the saved configuration, even while running", async () => {
  expect(await run(initArgs("--no-start"), deps)).toBe(0);
  const path = join(directory, ".thc", "config.json");
  const saved = readFileSync(path, "utf8");
  deps.exec = () => Promise.resolve({ stdout: "active\n", stderr: "" });
  for (const [name, value] of [
    ["--sandbox", "read-only"],
    ["--user-id", "999"],
    ["--token-file", join(directory, "missing-token")],
  ] as const) {
    output = "";
    errors = "";
    expect(await run(["init", "--yes", name, value], deps)).toBe(3);
    expect(errors).toContain(`E_CONFIG: ${name} would change`);
    expect(errors).toContain(`fix: Edit ${path}`);
    expect(errors).toContain("thc stop");
    expect(output).not.toContain("Already initialized");
  }
  expect(readFileSync(path, "utf8")).toBe(saved);
  output = "";
  expect(await run(initArgs(), deps)).toBe(0);
  expect(output).toContain("Already initialized and running");
  // The running bridge keeps its start-time configuration; say so, so that an
  // edited config file is never mistaken for an applied one.
  expect(output).toContain("take effect after thc stop and thc init");
});

it("creates a project folder that does not exist yet", async () => {
  const target = join(directory, "projects", "new-agent");
  expect(await run(initArgs("--no-start", "--dir", target), deps), errors).toBe(
    0,
  );
  expect(existsSync(join(target, ".thc", "config.json"))).toBe(true);
  expect(readFileSync(join(target, ".thc", "version"), "utf8").trim()).toBe(
    "1.2.3",
  );
});
