// Stand-ins for the system tools thc init calls, run as
// `node install-tools.ts <tool> ...args` from PATH wrappers the install test writes.
// THC_FAKE_STATE names a folder for their logs; THC_FAKE_NODE_MODULES is the
// dependency folder a fake `npm ci` links into the installed package.
import { spawn, spawnSync } from "node:child_process";
import {
  appendFileSync,
  openSync,
  readFileSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

const [tool = "", ...args] = process.argv.slice(2);
const state = process.env.THC_FAKE_STATE ?? process.cwd();

function codex(): number {
  if (args[0] === "--version") process.stdout.write("codex-cli 0.0.0-fake\n");
  return 0;
}
function npm(): number {
  const modules = process.env.THC_FAKE_NODE_MODULES;
  if (args[0] !== "ci" || !modules) return 1;
  symlinkSync(modules, join(process.cwd(), "node_modules"));
  return 0;
}
// Splits a command line the way systemd reads ExecStart=: double-quoted words
// with backslash escapes, bare words, and %% for a literal percent sign.
function unitWords(line: string): string[] {
  return [...line.matchAll(/"((?:\\.|[^"\\])*)"|(\S+)/g)].map((match) =>
    (match[1]?.replace(/\\(.)/g, "$1") ?? match[2] ?? "").replace(/%%/g, "%"),
  );
}
function setting(unit: string, name: string): string {
  return new RegExp(`^${name}=(.*)$`, "m").exec(unit)?.[1] ?? "";
}
// `enable --now` checks the unit as systemd would, then starts its ExecStart
// detached in its WorkingDirectory, as the user manager does.
function enable(name: string): number {
  const path = join(process.env.HOME ?? "", ".config/systemd/user", name);
  const verify = spawnSync("systemd-analyze", ["--user", "verify", path], {
    encoding: "utf8",
  });
  writeFileSync(join(state, "verify.log"), `${verify.stdout}${verify.stderr}`);
  if (verify.status !== 0) {
    process.stderr.write(verify.stderr);
    return 1;
  }
  const unit = readFileSync(path, "utf8");
  const [command = "", ...rest] = unitWords(setting(unit, "ExecStart"));
  const log = openSync(join(state, "bridge.log"), "a");
  const child = spawn(command, rest, {
    cwd: setting(unit, "WorkingDirectory").replace(/%%/g, "%"),
    detached: true,
    stdio: ["ignore", log, log],
  });
  writeFileSync(join(state, "bridge.pid"), String(child.pid));
  child.unref();
  return 0;
}
function systemctl(): number {
  appendFileSync(join(state, "systemctl.log"), `${args.join(" ")}\n`);
  const [, command, ...rest] = args;
  if (command === "is-active") {
    process.stdout.write("inactive\n");
    return 3;
  }
  return command === "enable" ? enable(rest.at(-1) ?? "") : 0;
}
function loginctl(): number {
  process.stdout.write("yes\n");
  return 0;
}

const tools: Record<string, () => number> = { codex, npm, systemctl, loginctl };
process.exitCode = (tools[tool] ?? (() => 127))();
