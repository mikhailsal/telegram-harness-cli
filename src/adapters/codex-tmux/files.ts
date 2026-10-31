import { open, readdir, stat } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";

const metadata = z.object({
  type: z.literal("session_meta"),
  payload: z.object({
    id: z.string(),
    cwd: z.string(),
    originator: z.string(),
    source: z.literal("cli"),
  }),
});
export async function rolloutPaths(
  root: string,
  startedAt: number,
): Promise<string[]> {
  const start = Math.min(startedAt, Date.now()) - 86400000;
  const end = Math.max(startedAt, Date.now()) + 86400000;
  const dates: string[] = [];
  for (let day = start; day <= end; day += 86400000) {
    dates.push(new Date(day).toISOString().slice(0, 10).replaceAll("-", "/"));
  }
  const paths: string[] = [];
  for (let index = 0; index < dates.length; index += 32) {
    const groups = await Promise.all(
      dates.slice(index, index + 32).map((date) => listDay(join(root, date))),
    );
    paths.push(...groups.flat());
  }
  return paths;
}
async function listDay(directory: string): Promise<string[]> {
  try {
    const entries = await readdir(directory, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isFile() && entry.name.endsWith(".jsonl"))
      .map((entry) => join(directory, entry.name));
  } catch (error) {
    if (isMissing(error)) return [];
    throw error;
  }
}
export async function matchingRollout(
  path: string,
  cwd: string,
  originator: string,
  startedAt: number,
): Promise<string | undefined> {
  try {
    if ((await stat(path)).mtimeMs < startedAt - 5000) return undefined;
    const value = await readMetadata(path);
    if (value?.cwd !== cwd || value.originator !== originator) return undefined;
    return value.id;
  } catch (error) {
    if (isMissing(error)) return undefined;
    throw error;
  }
}
export async function rolloutIdentity(
  path: string,
  cwd: string,
  originator: string,
): Promise<string> {
  const value = await readMetadata(path);
  if (value?.cwd !== cwd || value.originator !== originator)
    throw new Error("Rollout identity mismatch");
  return value.id;
}
async function readMetadata(
  path: string,
): Promise<z.infer<typeof metadata>["payload"] | undefined> {
  const file = await open(path, "r");
  try {
    const buffer = Buffer.alloc(65536);
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
    const data = buffer.subarray(0, bytesRead);
    const end = data.indexOf(10);
    if (end < 0) return undefined;
    let first: unknown;
    try {
      first = JSON.parse(data.subarray(0, end).toString("utf8")) as unknown;
    } catch {
      return undefined;
    }
    const parsed = metadata.safeParse(first);
    return parsed.success ? parsed.data.payload : undefined;
  } finally {
    await file.close();
  }
}
function isMissing(error: unknown): boolean {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}
