import { execFile as execFileCallback, spawn } from "node:child_process";
import { readFile, realpath } from "node:fs/promises";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);
export interface PaneIdentity {
  paneId: string;
  pid: string;
  executable: string;
  startTime: string;
}
export class TmuxRunner {
  readonly server: string;
  constructor(server: string) {
    this.server = server;
  }
  async run(...args: string[]): Promise<string> {
    const { stdout } = await execFile("tmux", ["-L", this.server, ...args], {
      maxBuffer: 1024 * 1024,
    });
    return stdout;
  }
  async start(
    cwd: string,
    home: string,
    executable: string,
    sandbox: string,
    originator: string,
  ): Promise<PaneIdentity> {
    await this.run(
      "new-session",
      "-d",
      "-s",
      "codex",
      "-x",
      "160",
      "-y",
      "40",
      "-c",
      cwd,
      "-e",
      `CODEX_HOME=${home}`,
      "-e",
      `CODEX_INTERNAL_ORIGINATOR_OVERRIDE=${originator}`,
      executable,
      "--no-daemon",
      "-a",
      "never",
      "-s",
      sandbox,
    );
    return this.identify(executable);
  }
  private async identify(executable: string): Promise<PaneIdentity> {
    const [paneId, pid] = (
      await this.run(
        "display-message",
        "-p",
        "-t",
        "codex",
        "#{pane_id} #{pane_pid}",
      )
    )
      .trim()
      .split(" ");
    if (!paneId || !pid) throw new Error("Unable to identify Codex pane");
    const expected = await realpath(executable);
    for (let attempt = 0; attempt < 100; attempt++) {
      try {
        const actual = await realpath(`/proc/${pid}/exe`);
        if (actual === expected)
          return {
            paneId,
            pid,
            executable: actual,
            startTime: await processStart(pid),
          };
      } catch {
        /* Process may still be starting. */
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error(
      "Pane is not running the requested native Codex executable",
    );
  }
  async guard(identity: PaneIdentity): Promise<void> {
    const value = (
      await this.run(
        "display-message",
        "-p",
        "-t",
        identity.paneId,
        "#{pane_id} #{pane_pid}",
      )
    ).trim();
    if (
      value !== `${identity.paneId} ${identity.pid}` ||
      (await realpath(`/proc/${identity.pid}/exe`)) !== identity.executable ||
      (await processStart(identity.pid)) !== identity.startTime
    )
      throw new Error("Codex pane identity changed");
  }
  async capture(identity: PaneIdentity): Promise<string> {
    await this.guard(identity);
    return this.run("capture-pane", "-p", "-t", identity.paneId);
  }
  async guardWorkspace(identity: PaneIdentity, cwd: string): Promise<void> {
    await this.guard(identity);
    if ((await realpath(`/proc/${identity.pid}/cwd`)) !== cwd)
      throw new Error("Codex pane workspace changed");
  }
  async paste(identity: PaneIdentity, text: string): Promise<void> {
    await this.guard(identity);
    await this.load(text);
    await this.guard(identity);
    await this.run("paste-buffer", "-d", "-p", "-t", identity.paneId);
  }
  async key(identity: PaneIdentity, key: string): Promise<void> {
    await this.guard(identity);
    await this.run("send-keys", "-t", identity.paneId, key);
  }
  async load(text: string): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const child = spawn("tmux", ["-L", this.server, "load-buffer", "-"]);
      child.once("error", reject);
      child.once("close", (code) => {
        if (code === 0) resolve();
        else reject(new Error(`tmux load-buffer exited ${String(code)}`));
      });
      child.stdin.end(text);
    });
  }
}
async function processStart(pid: string): Promise<string> {
  const stat = await readFile(`/proc/${pid}/stat`, "utf8");
  const values = stat.slice(stat.lastIndexOf(")") + 2).split(" ");
  const started = values[19];
  if (!started) throw new Error("Cannot read process identity");
  return started;
}
