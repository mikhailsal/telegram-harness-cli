import { appendFile, mkdir, rename, stat } from "node:fs/promises";
import { join } from "node:path";
import { redact } from "./config.js";
import { isMissing } from "./state.js";

export interface RuntimeLog {
  write: (event: string, details?: Record<string, unknown>) => void;
  flush: () => Promise<void>;
}
export function runtimeLog(directory: string): RuntimeLog {
  const path = join(directory, ".thc", "bridge.log");
  let queue = Promise.resolve();
  const write = (event: string, details: Record<string, unknown> = {}) => {
    const line =
      redact(
        JSON.stringify({ ...details, at: new Date().toISOString(), event }),
      ) + "\n";
    queue = queue
      .then(async () => {
        await mkdir(join(directory, ".thc"), { recursive: true, mode: 0o700 });
        try {
          if ((await stat(path)).size > 5 * 1024 * 1024)
            await rename(path, `${path}.1`);
        } catch (error) {
          if (!isMissing(error)) throw error;
        }
        await appendFile(path, line, { mode: 0o600 });
      })
      .catch((error: unknown) => {
        process.stderr.write(
          redact(
            `Bridge log: ${error instanceof Error ? error.message : String(error)}\n`,
          ),
        );
      });
  };
  return { write, flush: () => queue };
}
