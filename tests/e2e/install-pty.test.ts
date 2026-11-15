import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { FakeCodexAppServer } from "../fakes/codex-app-server.js";
import { FakeServices } from "../fakes/servers.js";

// Value: protects=a first `thc init` typed by a person in a terminal, from the
// hidden token prompt to the bridge's first answer through the generated unit;
// fails_when=a prompt label is not on screen, the unit is rejected by
// systemd-analyze, or the bridge it starts cannot answer; why_new=unit tests
// mock the terminal, systemd and the bridge separately; seam=THC_TELEGRAM_API_ROOT,
// fake codex/npm/systemctl/loginctl on PATH; no_claim=real Telegram, real Codex,
// install.sh's download and a real systemd user manager are not exercised

// Runs a command on a pseudo-terminal: its screen goes to stdout, stdin to its keyboard.
const proxy = String.raw`
import os, pty, select, sys
pid, fd = pty.fork()
if pid == 0:
    os.execvp(sys.argv[1], sys.argv[1:])
inputs = [fd, 0]
while True:
    ready, _, _ = select.select(inputs, [], [])
    if fd in ready:
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        os.write(1, data)
    if 0 in ready:
        data = os.read(0, 4096)
        if data:
            os.write(fd, data)
        else:
            inputs.remove(0)
_, status = os.waitpid(pid, 0)
sys.exit(os.waitstatus_to_exitcode(status))
`;
const token = "123:fake-install-token";
const answer = "Fake Codex answer";

class Terminal {
  output = "";
  finished = false;
  readonly exited: Promise<number | null>;
  private readonly child: ChildProcessWithoutNullStreams;
  constructor(child: ChildProcessWithoutNullStreams) {
    this.child = child;
    child.stdout.on("data", (data: Buffer) => {
      this.output += data.toString("utf8");
    });
    child.stderr.on("data", (data: Buffer) => {
      this.output += data.toString("utf8");
    });
    this.exited = new Promise((done) =>
      child.once("exit", (code) => {
        this.finished = true;
        done(code);
      }),
    );
  }
  type(text: string): void {
    this.child.stdin.write(text);
  }
  // True when the text survives the last screen erase, as a person would see it.
  shows(text: string): boolean {
    return (this.output.split("\u001b[0J").at(-1) ?? "").includes(text);
  }
  kill(): void {
    this.child.kill("SIGKILL");
  }
}
async function until(
  check: () => boolean,
  what: string,
  terminal: Terminal,
): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (!check()) {
    if (terminal.finished || Date.now() > deadline)
      throw new Error(`Timed out waiting for ${what}:\n${terminal.output}`);
    await new Promise((done) => setTimeout(done, 50));
  }
}

let root: string;
let codexHome: string;
let services: FakeServices;
let codex: FakeCodexAppServer;
let terminal: Terminal | undefined;

beforeEach(async () => {
  root = mkdtempSync(join(tmpdir(), "thc-install-"));
  // Unix socket paths are limited to 107 bytes, so Codex's home stays short.
  codexHome = mkdtempSync(join(tmpdir().length < 40 ? tmpdir() : "/tmp", "c-"));
  services = new FakeServices();
  await services.start();
  codex = new FakeCodexAppServer(answer);
  await codex.start(
    join(codexHome, "app-server-control", "app-server-control.sock"),
  );
});
afterEach(async () => {
  terminal?.kill();
  const pid = join(root, "state", "bridge.pid");
  if (existsSync(pid)) {
    try {
      process.kill(-Number(readFileSync(pid, "utf8")), "SIGKILL");
    } catch {
      /* The bridge already exited. */
    }
  }
  await codex.close();
  await services.close();
  rmSync(root, { recursive: true, force: true });
  rmSync(codexHome, { recursive: true, force: true });
});

function fakeTools(): string {
  const bin = join(root, "bin");
  mkdirSync(bin);
  const tools = resolve("tests/fakes/install-tools.ts");
  for (const tool of ["codex", "npm", "systemctl", "loginctl"]) {
    writeFileSync(
      join(bin, tool),
      `#!/bin/sh\nexec "${process.execPath}" "${tools}" ${tool} "$@"\n`,
    );
    chmodSync(join(bin, tool), 0o755);
  }
  return bin;
}
// The bridge checks npm for updates once a day; a fresh cache keeps it offline.
function freshUpdateCache(project: string): void {
  const { version } = JSON.parse(readFileSync("package.json", "utf8")) as {
    version: string;
  };
  mkdirSync(join(project, ".thc"), { recursive: true });
  writeFileSync(
    join(project, ".thc", "update-cache.json"),
    JSON.stringify({
      checkedAt: new Date().toISOString(),
      currentVersion: version,
      latestVersion: version,
      updateAvailable: false,
    }),
  );
}
function runtimeDirectory(): string {
  const path = join(codexHome, "run");
  mkdirSync(path, { mode: 0o700 });
  return path;
}
function startInit(): Terminal {
  const project = join(root, "my project");
  const state = join(root, "state");
  mkdirSync(project);
  mkdirSync(state);
  freshUpdateCache(project);
  const env = {
    HOME: join(root, "home"),
    PATH: `${fakeTools()}:${dirname(process.execPath)}:/usr/bin:/bin`,
    TERM: "xterm",
    CODEX_HOME: codexHome,
    THC_TELEGRAM_API_ROOT: services.url,
    THC_FAKE_STATE: state,
    THC_FAKE_NODE_MODULES: resolve("node_modules"),
    // systemd-analyze --user needs a runtime directory even without a session.
    XDG_RUNTIME_DIR: runtimeDirectory(),
  };
  const child = spawn(
    "python3",
    ["-c", proxy, process.execPath, resolve("dist/cli/main.js"), "init"],
    { cwd: project, env },
  );
  return new Terminal(child);
}
function repliedWithId(): boolean {
  return services.delivered.some(
    ({ method, body }) =>
      method === "sendMessage" && body.chat_id === 1 && body.text === "1",
  );
}

it("installs from a terminal and answers the first message", async () => {
  const screen = (terminal = startInit());
  services.input(1, "what is my id?");
  await until(() => screen.shows("Bot token (hidden): "), "token", screen);
  screen.type(`${token}\n`);
  await until(() => screen.output.includes("@thc_fake_bot"), "bot", screen);
  await until(() => screen.shows("Your Telegram user ID: "), "id", screen);
  await until(repliedWithId, "the bot's ID reply", screen);
  services.updates.length = 0;
  screen.type("1\n");
  await until(() => screen.shows("Sandbox ("), "sandbox", screen);
  screen.type("workspace-write\n");
  await until(() => screen.output.includes("Bridge started"), "start", screen);
  services.input(2, "hello");
  expect(await screen.exited, screen.output).toBe(0);

  expect(screen.output).toMatch(/Working: first answer in \d+ s\./);
  expect(screen.output).toContain("Initialized");
  expect(screen.output).not.toContain(token.split(":")[1]);
  expect(readFileSync(join(root, "state", "verify.log"), "utf8")).not.toMatch(
    /thc-[0-9a-f]+\.service/,
  );
  expect(JSON.stringify(services.delivered)).toContain(answer);
}, 60_000);
