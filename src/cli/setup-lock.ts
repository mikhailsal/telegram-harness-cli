import { createHash } from "node:crypto";
import { join } from "node:path";
import { acquireDirectoryLock, AlreadyRunningError } from "../core/lock.js";
import { CliError } from "./errors.js";

/** Serialize setup work across processes without taking the bridge's runtime lock. */
export async function withSetupLock<T>(
  home: string,
  key: string,
  operation: () => Promise<T>,
  options: { timeoutMs?: number; retryMs?: number } = {},
): Promise<T> {
  const name = createHash("sha256").update(key).digest("hex");
  const directory = join(home, ".local", "share", "thc", "setup-locks", name);
  const deadline = Date.now() + (options.timeoutMs ?? 120_000);
  for (;;) {
    let lease: Awaited<ReturnType<typeof acquireDirectoryLock>>;
    try {
      lease = await acquireDirectoryLock(directory);
    } catch (error) {
      if (!(error instanceof AlreadyRunningError)) throw error;
      if (Date.now() >= deadline)
        throw new CliError(
          "E_DEPENDENCY",
          "Timed out waiting for another setup operation",
          "Wait for the other setup to finish, then retry",
          4,
        );
      await new Promise((resolve) =>
        setTimeout(resolve, options.retryMs ?? 50),
      );
      continue;
    }
    try {
      return await operation();
    } finally {
      await lease.release();
    }
  }
}
