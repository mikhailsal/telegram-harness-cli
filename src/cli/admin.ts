import { createHash } from "node:crypto";
import { renderOutgoing } from "../channels/index.js";
import { open, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { readLockOwner, stopDirectory } from "../core/lock.js";
import { loadConfig, resolveToken, type Config } from "../core/config.js";
import { readUpdateCache } from "../core/update.js";
import { Store } from "../store/index.js";
import { allowedFlags, flag, positive, type Arguments } from "./arguments.js";
import type { CliDependencies } from "./context.js";
import { CliError } from "./errors.js";
import { exists } from "./paths.js";
import { redactSecrets } from "./secrets.js";

async function availableUpdate(directory: string): Promise<string | null> {
  try {
    const config = await loadConfig(directory);
    if (!config.update_check) return null;
    const cache = await readUpdateCache(directory);
    if (!cache?.updateAvailable) return null;
    const version = (
      await readFile(join(directory, ".thc", "version"), "utf8")
    ).trim();
    return version === cache.currentVersion ? cache.latestVersion : null;
  } catch {
    return null;
  }
}
export async function status(
  directory: string,
): Promise<Record<string, unknown>> {
  const owner = await readLockOwner(directory);
  const defaultMode = await loadConfig(directory).then(
    (config) => config.mode,
    () => "queue" as const,
  );
  const path = join(directory, ".thc", "store.db");
  const updateAvailable = await availableUpdate(directory);
  const updateCommand = updateAvailable
    ? "npx telegram-harness-cli@latest init --update"
    : null;
  if (!(await exists(path)))
    return {
      running: Boolean(owner),
      pid: owner?.pid ?? null,
      failed: 0,
      pending: 0,
      updateAvailable,
      updateCommand,
    };
  const store = new Store(path, true);
  try {
    return {
      running: Boolean(owner),
      pid: owner?.pid ?? null,
      failed: store.listOutbox(["failed"]).length,
      pending: store.listRequests(["pending", "answering"]).length,
      mode: store.getCursor("followup-mode") ?? defaultMode,
      activeTurns: store.listTurns(["submitting", "active", "uncertain"])
        .length,
      pollConflict: store.getCursor("poll-conflict") === "1",
      rolloutMismatches: Number(store.getCursor("rollout-mismatches") ?? "0"),
      markupWarnings: Number(store.getCursor("markup-warnings") ?? "0"),
      updateAvailable,
      updateCommand,
      diagnosticsRecorded:
        store.getCursor("poll-conflict") !== undefined ||
        store.getCursor("rollout-mismatches") !== undefined ||
        store.getCursor("markup-warnings") !== undefined,
    };
  } finally {
    store.close();
  }
}
export async function stop(
  directory: string,
  deps: CliDependencies,
): Promise<void> {
  deps.stdout(
    (await stopDirectory(directory))
      ? "Stop requested.\n"
      : "Bridge is not running.\n",
  );
}
async function checkProgram(
  file: string,
  args: string[],
  directory: string,
  deps: CliDependencies,
): Promise<boolean> {
  try {
    await deps.exec(file, args, {
      cwd: directory,
      env: deps.env,
      timeout: 5000,
    });
    return true;
  } catch {
    return false;
  }
}
async function versionCheck(
  config: Config,
  directory: string,
  deps: CliDependencies,
): Promise<{
  version: string | null;
  fixture: string;
  matches: boolean | null;
}> {
  const fixture = "0.166.0";
  try {
    const result = await deps.exec(config.codex.executable, ["--version"], {
      cwd: directory,
      env: deps.env,
      timeout: 5000,
    });
    const version = /\b(\d+\.\d+\.\d+)\b/.exec(result.stdout)?.[1] ?? null;
    return {
      version,
      fixture,
      matches: version === null ? null : version === fixture,
    };
  } catch {
    return { version: null, fixture, matches: null };
  }
}
async function probe(
  directory: string,
  config: Config,
  deps: CliDependencies,
): Promise<string> {
  if (!(await readLockOwner(directory)))
    return "unavailable: bridge is not running";
  try {
    const store = new Store(join(directory, ".thc", "store.db"), true);
    try {
      const execute =
        deps.probeHistory ??
        (await import("../core/adapter.js")).probeAdapterSearch;
      const result = await execute(directory, config, store);
      return result.exitCode === 0 ? "ok" : "failed";
    } finally {
      store.close();
    }
  } catch {
    return "failed or unsupported";
  }
}
function recentUsage(
  line: string,
  now: number,
): { empty: boolean; error: boolean } | undefined {
  try {
    const row: unknown = JSON.parse(line);
    if (
      typeof row !== "object" ||
      row === null ||
      !("time" in row) ||
      typeof row.time !== "string" ||
      Date.parse(row.time) < now - 7 * 86400000
    )
      return;
    return {
      empty: "hits" in row && row.hits === 0,
      error: "error" in row && row.error !== null,
    };
  } catch {
    return;
  }
}
async function usageSummary(
  directory: string,
  now: number,
): Promise<{
  calls: number;
  empty: number;
  errors: number;
  recording: string;
}> {
  const path = join(directory, ".thc", "usage.jsonl");
  if (!(await exists(path)))
    return { calls: 0, empty: 0, errors: 0, recording: "no usage recorded" };
  try {
    const lines = (await readFile(path, "utf8")).split("\n");
    let calls = 0,
      empty = 0,
      errors = 0;
    for (const line of lines) {
      const row = recentUsage(line, now);
      if (!row) continue;
      calls++;
      if (row.empty) empty++;
      if (row.error) errors++;
    }
    return { calls, empty, errors, recording: "available" };
  } catch {
    return { calls: 0, empty: 0, errors: 0, recording: "unreadable" };
  }
}
async function skillStatus(directory: string, root: string): Promise<string> {
  const target = join(directory, ".agents", "skills", "thc", "SKILL.md");
  const marker = join(directory, ".thc", "skill.sha256");
  if (!(await exists(target)) || !(await exists(marker)))
    return "missing or unmanaged";
  try {
    const hash = (text: string): string =>
      createHash("sha256").update(text).digest("hex");
    const current = hash(await readFile(target, "utf8"));
    const recorded = (await readFile(marker, "utf8")).trim();
    if (current !== recorded)
      return "modified by user; thc init --update --force";
    return current ===
      hash(
        await readFile(
          join(root, "skills", "telegram-history", "SKILL.md"),
          "utf8",
        ),
      )
      ? "current"
      : "outdated";
  } catch {
    return "unreadable";
  }
}
export async function doctor(
  directory: string,
  config: Config,
  deps: CliDependencies,
): Promise<Record<string, unknown>> {
  const checks: Record<string, unknown> = {};
  try {
    await resolveToken(config.token, directory, deps.env);
    checks.token = "ok";
  } catch {
    checks.token = "invalid";
  }
  checks.allowlist = config.allowlist.length > 0 ? "ok" : "invalid";
  checks.codex = await checkProgram(
    config.codex.executable,
    ["--version"],
    directory,
    deps,
  );
  checks.version = await versionCheck(config, directory, deps);
  checks.codexLogin = await checkProgram(
    config.codex.executable,
    ["login", "status"],
    directory,
    deps,
  );
  checks.tmux = await checkProgram("tmux", ["-V"], directory, deps);
  try {
    const linger = await deps.exec(
      "loginctl",
      [
        "show-user",
        String(process.getuid?.() ?? ""),
        "-p",
        "Linger",
        "--value",
      ],
      { cwd: directory, env: deps.env, timeout: 5000 },
    );
    checks.linger = linger.stdout.trim() === "yes";
  } catch {
    checks.linger = false;
  }
  checks.status = await status(directory);
  checks.sandbox = config.sandbox;
  checks.searchProbe = await probe(directory, config, deps);
  checks.usage = await usageSummary(directory, deps.now());
  checks.skill = await skillStatus(directory, deps.packageRoot);
  return checks;
}
export async function tail(
  args: Arguments,
  directory: string,
  deps: CliDependencies,
): Promise<void> {
  allowedFlags(args, ["lines"]);
  const count = positive(flag(args, "lines"), 100, 1000);
  const path = join(directory, ".thc", "bridge.log");
  if (!(await exists(path))) {
    deps.stdout("No log yet.\n");
    return;
  }
  const size = (await stat(path)).size;
  const length = Math.min(size, 1024 * 1024);
  const file = await open(path, "r");
  try {
    const buffer = Buffer.alloc(length);
    const result = await file.read(buffer, 0, length, size - length);
    const lines = buffer
      .subarray(0, result.bytesRead)
      .toString("utf8")
      .split("\n");
    if (size > length) lines.shift();
    deps.stdout(
      `${redactSecrets(lines.filter(Boolean).slice(-count).join("\n"), deps.env)}\n`,
    );
  } finally {
    await file.close();
  }
}
export function render(
  args: Arguments,
  directory: string,
  deps: CliDependencies,
): void {
  allowedFlags(args, ["turn"]);
  const turn = flag(args, "turn") ?? args.positionals[0];
  if (!turn)
    throw new CliError(
      "E_USAGE",
      "render requires --turn",
      "thc render --turn TURN_ID",
      2,
    );
  const store = new Store(join(directory, ".thc", "store.db"), true);
  try {
    const rows = store
      .messagesForTurn(turn)
      .map(({ author, sentAt, text, possiblyDuplicated }) => ({
        author,
        sentAt,
        text,
        possiblyDuplicated,
      }));
    deps.stdout(
      args.flags.has("json")
        ? `${JSON.stringify({ entries: rows })}\n`
        : `${rows.map((row) => `[${row.sentAt} · ${row.author}] ${row.text}`).join("\n")}\n`,
    );
  } finally {
    store.close();
  }
}
export async function renderMarkdown(
  args: Arguments,
  deps: CliDependencies,
): Promise<void> {
  allowedFlags(args, []);
  if (args.positionals.length > 1)
    throw new CliError(
      "E_USAGE",
      "Expected one Markdown file",
      "thc render --help",
      2,
    );
  const source = args.positionals[0]
    ? await readFile(args.positionals[0], "utf8")
    : await deps.readInput();
  const result = renderOutgoing(source);
  deps.stdout(
    args.flags.has("json")
      ? `${JSON.stringify(result)}\n`
      : `${result.chunks.map((chunk) => chunk.text).join("\n\n")}\n`,
  );
}
