import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { readUpdateCache, refreshUpdateCache } from "../../src/core/update.js";

let directory: string;
const start = Date.parse("2026-10-31T00:00:00Z");
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-update-"));
});
afterEach(() => {
  rmSync(directory, { recursive: true, force: true });
});
it("caches the newest release for a day and updates the comparison for a changed current version", async () => {
  let calls = 0;
  const fetcher: typeof fetch = () => {
    calls++;
    return Promise.resolve(
      new Response(JSON.stringify({ version: "2.0.0" }), { status: 200 }),
    );
  };
  const first = await refreshUpdateCache(directory, "1.0.0", {
    enabled: true,
    fetch: fetcher,
    now: () => start,
  });
  expect(first).toMatchObject({
    latestVersion: "2.0.0",
    updateAvailable: true,
  });
  const cached = await refreshUpdateCache(directory, "2.0.0", {
    enabled: true,
    fetch: fetcher,
    now: () => start + 1000,
  });
  expect(cached).toMatchObject({
    currentVersion: "2.0.0",
    updateAvailable: false,
  });
  expect(calls).toBe(1);
  await refreshUpdateCache(directory, "1.0.0", {
    enabled: true,
    fetch: fetcher,
    now: () => start + 86400000,
  });
  expect(calls).toBe(2);
});
it("keeps the last known version after network failure and caches that failure", async () => {
  const good: typeof fetch = () =>
    Promise.resolve(
      new Response(JSON.stringify({ version: "1.2.0" }), { status: 200 }),
    );
  await refreshUpdateCache(directory, "1.0.0", {
    enabled: true,
    fetch: good,
    now: () => start,
  });
  let calls = 0;
  const offline: typeof fetch = () => {
    calls++;
    return Promise.reject(new Error("offline"));
  };
  const result = await refreshUpdateCache(directory, "1.0.0", {
    enabled: true,
    fetch: offline,
    now: () => start + 86400000,
  });
  expect(result).toMatchObject({
    latestVersion: "1.2.0",
    updateAvailable: true,
  });
  await refreshUpdateCache(directory, "1.0.0", {
    enabled: true,
    fetch: offline,
    now: () => start + 86401000,
  });
  expect(calls).toBe(1);
  expect(await readUpdateCache(directory)).toMatchObject({
    latestVersion: "1.2.0",
  });
  expect(
    readFileSync(join(directory, ".thc", "update-cache.json"), "utf8"),
  ).not.toContain("offline");
});
it("respects opt-out and handles unknown releases without reporting a false update", async () => {
  let calls = 0;
  const missing: typeof fetch = () => {
    calls++;
    return Promise.resolve(new Response("", { status: 404 }));
  };
  expect(
    await refreshUpdateCache(directory, "1.0.0", {
      enabled: false,
      fetch: missing,
      now: () => start,
    }),
  ).toBeUndefined();
  expect(calls).toBe(0);
  expect(
    await refreshUpdateCache(directory, "1.0.0", {
      enabled: true,
      fetch: missing,
      now: () => start,
    }),
  ).toMatchObject({ latestVersion: null, updateAvailable: false });
  expect(calls).toBe(1);
});
it("compares prereleases against stable releases", async () => {
  const fetcher: typeof fetch = () =>
    Promise.resolve(
      new Response(JSON.stringify({ version: "1.0.0" }), { status: 200 }),
    );
  expect(
    await refreshUpdateCache(directory, "1.0.0-beta.2", {
      enabled: true,
      fetch: fetcher,
      now: () => start,
    }),
  ).toMatchObject({ updateAvailable: true });
  expect(
    await refreshUpdateCache(directory, "1.0.0", {
      enabled: true,
      fetch: fetcher,
      now: () => start + 1,
    }),
  ).toMatchObject({ updateAvailable: false });
});

it("orders numeric and textual prerelease segments using release ordering", async () => {
  const fetcher: typeof fetch = () =>
    Promise.resolve(
      new Response(JSON.stringify({ version: "1.0.0-beta.10" }), {
        status: 200,
      }),
    );
  expect(
    await refreshUpdateCache(directory, "1.0.0-beta.2", {
      enabled: true,
      fetch: fetcher,
      now: () => start,
    }),
  ).toMatchObject({ updateAvailable: true });
  expect(
    await refreshUpdateCache(directory, "1.0.0-beta.alpha", {
      enabled: true,
      fetch: fetcher,
      now: () => start + 1,
    }),
  ).toMatchObject({ updateAvailable: false });
  expect(
    await refreshUpdateCache(directory, "1.0.0-beta", {
      enabled: true,
      fetch: fetcher,
      now: () => start + 2,
    }),
  ).toMatchObject({ updateAvailable: true });
});
it("ignores malformed registry replies and does not fail startup on an unwritable cache", async () => {
  const { writeFileSync } = await import("node:fs");
  const occupied = join(directory, "occupied");
  writeFileSync(occupied, "not a directory");
  const malformed: typeof fetch = () =>
    Promise.resolve(
      new Response(JSON.stringify({ version: "not-a-version" }), {
        status: 200,
      }),
    );
  expect(
    await refreshUpdateCache(occupied, "1.0.0", {
      enabled: true,
      fetch: malformed,
      now: () => start,
    }),
  ).toMatchObject({ latestVersion: null, updateAvailable: false });
  expect(await readUpdateCache(occupied)).toBeUndefined();
  expect(
    await refreshUpdateCache(directory, "invalid", {
      enabled: true,
      fetch: malformed,
      now: () => start,
    }),
  ).toBeUndefined();
});
