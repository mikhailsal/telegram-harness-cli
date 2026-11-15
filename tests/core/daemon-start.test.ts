import { expect, it, vi } from "vitest";
import {
  daemonCommand,
  startDaemon,
  type DaemonExec,
} from "../../src/core/daemon-start.js";

function options(env: NodeJS.ProcessEnv, exec: DaemonExec) {
  const log = vi.fn();
  const controller = new AbortController();
  return {
    log,
    controller,
    value: {
      executable: "/opt/codex/bin/codex",
      directory: "/work/project",
      env,
      signal: controller.signal,
      log,
      exec,
    },
  };
}

it("starts the daemon in its own systemd scope when the bridge is a service", async () => {
  const exec = vi.fn<DaemonExec>().mockResolvedValue(undefined);
  const { value, controller } = options({ INVOCATION_ID: "abc" }, exec);
  await startDaemon(value);
  // protects=under systemd the daemon leaves the bridge cgroup via a transient scope; fails_when=Codex is executed directly or with other systemd-run flags; no_claim=that systemd accepts the command (exec is a fake)
  expect(exec).toHaveBeenCalledWith(
    "systemd-run",
    [
      "--user",
      "--scope",
      "--collect",
      "--quiet",
      "--description=Codex app-server daemon (started by thc)",
      "--",
      "/opt/codex/bin/codex",
      "app-server",
      "daemon",
      "start",
    ],
    {
      cwd: "/work/project",
      timeout: 30000,
      maxBuffer: 1024 * 1024,
      signal: controller.signal,
    },
  );
});

it("starts Codex directly outside systemd", () => {
  // protects=a foreground bridge keeps the direct start; fails_when=systemd-run is required without a service; no_claim=none beyond protects
  expect(daemonCommand("codex", {})).toEqual({
    file: "codex",
    args: ["app-server", "daemon", "start"],
    isolated: false,
  });
});

it("does not fall back to a direct start when the isolated start fails", async () => {
  const failure = Object.assign(new Error("spawn systemd-run ENOENT"), {
    code: "ENOENT",
  });
  const exec = vi.fn<DaemonExec>().mockRejectedValue(failure);
  const { value, log } = options({ INVOCATION_ID: "abc" }, exec);
  // protects=a failed isolated start is reported and retried by the supervisor, never replaced by a direct start; fails_when=Codex is started inside the bridge unit or the failure is silent; no_claim=supervisor retry timing (not exercised here)
  await expect(startDaemon(value)).rejects.toBe(failure);
  expect(exec).toHaveBeenCalledTimes(1);
  expect(log).toHaveBeenCalledExactlyOnceWith("daemon.start-failed", {
    isolated: true,
    code: "ENOENT",
  });
});

it("passes the runtime abort signal and stays quiet when shutdown cancels the start", async () => {
  const exec = vi.fn<DaemonExec>((_file, _args, { signal }) => {
    return new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        reject(Object.assign(new Error("aborted"), { code: "ABORT_ERR" }));
      });
    });
  });
  const { value, controller, log } = options({}, exec);
  const started = startDaemon(value);
  controller.abort();
  // protects=bridge shutdown cancels the start through the exec signal without a failure event; fails_when=the signal is not passed or shutdown is logged as a failure; no_claim=killing of real child processes (exec is a fake)
  await expect(started).rejects.toThrow("aborted");
  expect(log).not.toHaveBeenCalled();
});

it("reports exit statuses and unknown failures by code only", async () => {
  const exec = vi
    .fn<DaemonExec>()
    .mockRejectedValueOnce(Object.assign(new Error("exit"), { code: 1 }))
    .mockRejectedValueOnce("boom");
  const { value, log } = options({ INVOCATION_ID: "abc" }, exec);
  await expect(startDaemon(value)).rejects.toThrow("exit");
  await expect(startDaemon(value)).rejects.toBe("boom");
  // protects=the log carries a numeric exit status or "unknown", never stderr text; fails_when=other details leak into the event; no_claim=none beyond protects
  expect(log.mock.calls).toEqual([
    ["daemon.start-failed", { isolated: true, code: 1 }],
    ["daemon.start-failed", { isolated: true, code: "unknown" }],
  ]);
});
