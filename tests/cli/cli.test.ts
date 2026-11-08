import {
  mkdtempSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { run, type CliDependencies } from "../../src/cli/main.js";
import { openStore } from "../../src/store/index.js";

let directory: string;
let output: string;
let errors: string;
let deps: Partial<CliDependencies>;
const version = "1.2.3";
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-cli-"));
  output = "";
  errors = "";
  const pkg = join(directory, "package");
  for (const path of ["dist/cli", "node_modules", "skills/telegram-history"])
    mkdirSync(join(pkg, path), { recursive: true });
  writeFileSync(join(pkg, "package.json"), JSON.stringify({ version }));
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
    exec: () => Promise.resolve({ stdout: "ok", stderr: "" }),
  };
});
afterEach(() => {
  rmSync(directory, { recursive: true, force: true });
});

it("provides two examples for each command without external side effects", async () => {
  for (const command of [
    "init",
    "start",
    "stop",
    "status",
    "doctor",
    "tail",
    "render",
    "search",
    "around",
    "day",
    "explain",
    "attach",
  ]) {
    output = "";
    expect(await run([command, "--help"], deps)).toBe(0);
    expect(output.match(/^ {2}thc /gm)).toHaveLength(2);
  }
});
it("renders Markdown from standard input without a configured folder", async () => {
  deps.readInput = () =>
    Promise.resolve(
      "Visible\n\n<internal-note>secret</internal-note>\n\n`<internal-note>literal</internal-note>`",
    );
  expect(await run(["render"], deps)).toBe(0);
  expect(output).toContain("Visible");
  expect(output).toContain("literal");
  expect(output).not.toContain("secret");
});
it("reports missing noninteractive inputs using the stable JSON error", async () => {
  expect(await run(["init", "--json", "--no-start"], deps)).toBe(2);
  const parsed: unknown = JSON.parse(output);
  expect(parsed).toMatchObject({ error: { code: "E_NEEDS_INPUT" } });
  expect(output).toContain("--token-file");
  expect(errors).toBe("");
});
it("initializes with a private token file and does not enable a service with --no-start", async () => {
  const calls: string[] = [];
  deps.exec = (file, args) => {
    calls.push([file, ...args].join(" "));
    return Promise.resolve({ stdout: "ok", stderr: "" });
  };
  expect(
    await run(
      [
        "init",
        "--token-file",
        join(directory, "token"),
        "--user-id",
        "123",
        "--sandbox",
        "workspace-write",
        "--yes",
        "--no-start",
      ],
      deps,
    ),
  ).toBe(0);
  expect(calls.every((call) => !call.startsWith("systemctl"))).toBe(true);
  const config: unknown = JSON.parse(
    readFileSync(join(directory, ".thc", "config.json"), "utf8"),
  );
  expect(config).toMatchObject({
    sandbox: "workspace-write",
    allowlist: [123],
    token: { file: join(directory, "token") },
  });
  expect(readFileSync(join(directory, ".thc", "version"), "utf8").trim()).toBe(
    version,
  );
  expect(readFileSync(join(directory, "AGENTS.md"), "utf8")).toContain(
    "<!-- thc:begin -->",
  );
  expect(output).not.toContain("abcdefghijklmnopqrstuvwxyz");
});
it.each([
  {
    path: '/synthetic/space dir/%"\\bin:/usr/bin',
    expected: 'Environment="PATH=/synthetic/space dir/%%\\"\\\\bin:/usr/bin"',
  },
  { path: undefined, expected: undefined },
])(
  "writes only the supplied PATH to the service environment: $path",
  async ({ path, expected }) => {
    deps.env = { PATH: path, SYNTHETIC_SECRET: "do-not-copy" };
    expect(
      await run(
        [
          "init",
          "--token-file",
          join(directory, "token"),
          "--user-id",
          "123",
          "--sandbox",
          "read-only",
          "--no-start",
        ],
        deps,
      ),
    ).toBe(0);
    const units = join(directory, "home", ".config", "systemd", "user");
    const [unitFile] = readdirSync(units);
    if (unitFile === undefined)
      throw new Error("Service unit was not generated");
    const unit = readFileSync(join(units, unitFile), "utf8");
    const environment = unit
      .split("\n")
      .filter((line) => line.startsWith("Environment="));
    expect(environment).toEqual(expected === undefined ? [] : [expected]);
    expect(unit).not.toContain("do-not-copy");
  },
);
it("refuses malformed sandbox before writing configuration", async () => {
  expect(
    await run(
      [
        "init",
        "--token-file",
        join(directory, "token"),
        "--user-id",
        "123",
        "--sandbox",
        "unrestricted",
        "--no-start",
      ],
      deps,
    ),
  ).toBe(3);
  expect(errors).toContain("E_CONFIG");
  expect(() => readFileSync(join(directory, ".thc", "config.json"))).toThrow();
});
it("rejects a PATH containing a newline when writing the unit", async () => {
  deps.env = { PATH: "/synthetic/bin\nInjected=value" };
  expect(
    await run(
      [
        "init",
        "--token-file",
        join(directory, "token"),
        "--user-id",
        "123",
        "--sandbox",
        "read-only",
        "--no-start",
      ],
      deps,
    ),
  ).toBe(2);
  expect(errors).toContain("E_USAGE");
});
it("renders only visible history without bridge identifiers", async () => {
  mkdirSync(join(directory, ".thc"));
  writeFileSync(
    join(directory, ".thc", "config.json"),
    JSON.stringify({
      token: { file: join(directory, "token") },
      allowlist: [123],
      sandbox: "read-only",
    }),
  );
  const store = openStore(join(directory, ".thc", "store.db"));
  store.recordVisibleMessage({
    id: "secret-internal-id",
    chatId: "c",
    telegramMessageId: 1,
    author: "user",
    sentAt: "2026-10-31T10:00:00Z",
    text: "visible message",
    turnId: null,
    possiblyDuplicated: false,
  });
  store.close();
  expect(await run(["search", "visible", "--json"], deps)).toBe(0);
  expect(output).toContain("visible message");
  expect(output).not.toContain("secret-internal-id");
});

it("uses the configured timezone for day boundaries", async () => {
  mkdirSync(join(directory, ".thc"));
  writeFileSync(
    join(directory, ".thc", "config.json"),
    JSON.stringify({
      token: { file: join(directory, "token") },
      allowlist: [123],
      sandbox: "read-only",
      timezone: "Europe/Moscow",
    }),
  );
  const store = openStore(join(directory, ".thc", "store.db"));
  store.recordVisibleMessage({
    id: "hidden-id",
    chatId: "c",
    telegramMessageId: 1,
    author: "user",
    sentAt: "2026-10-30T23:30:00.000Z",
    text: "after local midnight",
    turnId: null,
    possiblyDuplicated: false,
  });
  store.close();
  expect(await run(["day", "2026-10-31"], deps)).toBe(0);
  expect(output).toContain("after local midnight");
  expect(output).not.toContain("hidden-id");
});

it("waits for a delivered answer, not the runtime-ready signal", async () => {
  const at = Date.parse("2026-10-31T12:00:00.000Z");
  let clock = at;
  let sleeps = 0;
  deps.now = () => clock;
  deps.sleep = (milliseconds) => {
    clock += milliseconds;
    sleeps++;
    const store = openStore(join(directory, ".thc", "store.db"));
    try {
      store.setCursor("runtime-ready", new Date(clock).toISOString());
      if (sleeps === 2)
        store.setCursor("first-delivery-at", new Date(clock).toISOString());
    } finally {
      store.close();
    }
    return Promise.resolve();
  };
  expect(
    await run(
      [
        "init",
        "--token-file",
        join(directory, "token"),
        "--user-id",
        "123",
        "--sandbox",
        "workspace-write",
        "--yes",
      ],
      deps,
    ),
  ).toBe(0);
  expect(sleeps).toBe(2);
  expect(output).toContain("first answer in 1 s");
});

it("preserves a hand-edited skill on update unless force is explicit", async () => {
  const args = [
    "init",
    "--token-file",
    join(directory, "token"),
    "--user-id",
    "123",
    "--sandbox",
    "read-only",
    "--yes",
    "--no-start",
  ];
  expect(await run(args, deps)).toBe(0);
  const skill = join(directory, ".agents", "skills", "thc", "SKILL.md");
  writeFileSync(skill, "My own instructions\n");
  expect(await run(["init", "--update", "--no-start"], deps)).toBe(0);
  expect(readFileSync(skill, "utf8")).toBe("My own instructions\n");
  expect(await run(["init", "--update", "--force", "--no-start"], deps)).toBe(
    0,
  );
  expect(readFileSync(skill, "utf8")).toBe("# History\n");
});

it("runs doctor after the first-answer deadline without claiming success", async () => {
  const at = Date.parse("2026-10-31T12:00:00Z");
  let now = at;
  deps.now = () => now;
  deps.sleep = () => {
    now += 120000;
    return Promise.resolve();
  };
  expect(
    await run(
      [
        "init",
        "--token-file",
        join(directory, "token"),
        "--user-id",
        "123",
        "--sandbox",
        "read-only",
        "--yes",
      ],
      deps,
    ),
  ).toBe(0);
  expect(output).toContain("No first answer within 120 s");
  expect(output).not.toContain("Working: first answer");
});

it("rejects token files readable by another user", async () => {
  const { chmodSync } = await import("node:fs");
  chmodSync(join(directory, "token"), 0o644);
  expect(
    await run(
      [
        "init",
        "--token-file",
        join(directory, "token"),
        "--user-id",
        "123",
        "--sandbox",
        "read-only",
        "--no-start",
      ],
      deps,
    ),
  ).toBe(3);
  expect(errors).toContain("E_CONFIG");
  expect(output).not.toContain("abcdefghijklmnop");
});
