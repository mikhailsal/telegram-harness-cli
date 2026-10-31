import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { atomicJson } from "./state.js";

const version = z
  .string()
  .regex(/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/);
const cacheSchema = z.strictObject({
  checkedAt: z.iso.datetime({ offset: true }),
  currentVersion: version,
  latestVersion: version.nullable(),
  updateAvailable: z.boolean(),
});
export type UpdateInfo = z.infer<typeof cacheSchema>;
export interface UpdateOptions {
  enabled: boolean;
  fetch?: typeof fetch;
  now?: () => number;
}
const CACHE_MS = 24 * 60 * 60 * 1000;
const URL = "https://registry.npmjs.org/telegram-harness-cli/latest";

export async function readUpdateCache(
  directory: string,
): Promise<UpdateInfo | undefined> {
  try {
    const data: unknown = JSON.parse(
      await readFile(join(directory, ".thc", "update-cache.json"), "utf8"),
    );
    const result = cacheSchema.safeParse(data);
    return result.success ? result.data : undefined;
  } catch {
    return;
  }
}
function compareIdentifier(x: string, y: string): number {
  if (x === y) return 0;
  const xn = /^\d+$/.test(x),
    yn = /^\d+$/.test(y);
  if (xn && yn) return Number(x) > Number(y) ? 1 : -1;
  if (xn !== yn) return xn ? -1 : 1;
  return x > y ? 1 : -1;
}
function prerelease(
  left: string | undefined,
  right: string | undefined,
): number {
  if (left === undefined) return right === undefined ? 0 : 1;
  if (right === undefined) return -1;
  const a = left.split("."),
    b = right.split(".");
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i],
      y = b[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const order = compareIdentifier(x, y);
    if (order) return order;
  }
  return 0;
}
function newer(latest: string, current: string): boolean {
  const [left, leftPre] = latest.split("+", 1)[0]?.split("-", 2) ?? [];
  const [right, rightPre] = current.split("+", 1)[0]?.split("-", 2) ?? [];
  if (!left || !right) return false;
  const a = left.split(".").map(Number),
    b = right.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    const x = a[i] ?? 0,
      y = b[i] ?? 0;
    if (x !== y) return x > y;
  }
  return prerelease(leftPre, rightPre) > 0;
}
async function latestVersion(
  fetcher: typeof fetch,
): Promise<string | undefined> {
  const response = await fetcher(URL, {
    signal: AbortSignal.timeout(5000),
    headers: { accept: "application/json" },
  });
  if (!response.ok) return;
  const body: unknown = await response.json();
  if (typeof body !== "object" || body === null || !("version" in body)) return;
  const parsed = version.safeParse(body.version);
  return parsed.success ? parsed.data : undefined;
}
function withCurrent(cache: UpdateInfo, currentVersion: string): UpdateInfo {
  return {
    ...cache,
    currentVersion,
    updateAvailable:
      cache.latestVersion !== null &&
      newer(cache.latestVersion, currentVersion),
  };
}
function fresh(cache: UpdateInfo | undefined, now: number): boolean {
  if (!cache) return false;
  const age = now - Date.parse(cache.checkedAt);
  return age >= 0 && age < CACHE_MS;
}
async function fetchLatest(fetcher: typeof fetch): Promise<string | undefined> {
  try {
    return await latestVersion(fetcher);
  } catch {
    return;
  }
}
function updatedInfo(
  now: number,
  currentVersion: string,
  latest: string | undefined,
  cached: UpdateInfo | undefined,
): UpdateInfo {
  const latestVersion = latest ?? cached?.latestVersion ?? null;
  return {
    checkedAt: new Date(now).toISOString(),
    currentVersion,
    latestVersion,
    updateAvailable:
      latestVersion !== null && newer(latestVersion, currentVersion),
  };
}
export async function refreshUpdateCache(
  directory: string,
  currentVersion: string,
  options: UpdateOptions,
): Promise<UpdateInfo | undefined> {
  if (!options.enabled || !version.safeParse(currentVersion).success) return;
  const existing = await readUpdateCache(directory);
  const now = (options.now ?? Date.now)();
  const cached = existing ? withCurrent(existing, currentVersion) : undefined;
  if (cached && fresh(cached, now)) return cached;
  const latest = await fetchLatest(options.fetch ?? fetch);
  const result = updatedInfo(now, currentVersion, latest, cached);
  try {
    await atomicJson(join(directory, ".thc", "update-cache.json"), result);
  } catch {
    /* Cache failure must not interrupt the bridge. */
  }
  return result;
}
