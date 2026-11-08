import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import {
  ConfigurationError,
  codexHome,
  loadConfig,
  noticeCredentials,
  parseConfig,
  redact,
  resolveToken,
} from "../../src/core/config.ts";
const valid = {
  token: { env: "BOT_TOKEN" },
  allowlist: [123],
  sandbox: "read-only",
};
it("validates strict configuration without exposing secret values", () => {
  expect(parseConfig(valid)).toMatchObject({
    timezone: "UTC",
    codex: { adapter: "protocol" },
  });
  for (const input of [
    { ...valid, mystery: "secret-value" },
    { ...valid, allowlist: [] },
    { ...valid, timezone: "wrong/timezone" },
    { ...valid, token: { env: "BAD-NAME" } },
    { ...valid, codex: { unexpected: true } },
  ]) {
    expect(() => parseConfig(input)).toThrow(ConfigurationError);
  }
  expect(redact("request bot123:secret and 123:secret")).toBe(
    "request bot<redacted> and <redacted>",
  );
  expect(noticeCredentials({ ...valid, unsupported: true })).toEqual({
    token: valid.token,
    allowlist: valid.allowlist,
  });
  expect(noticeCredentials(null)).toBeUndefined();
  expect(noticeCredentials({ token: {}, allowlist: [] })).toBeUndefined();
  expect(
    codexHome(
      parseConfig({ ...valid, codex: { home: "/tmp/custom-codex-home" } }),
    ),
  ).toBe("/tmp/custom-codex-home");
});
it("reads config and enforces private token files and environment validation", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-config-"));
  try {
    await mkdir(join(root, ".thc"));
    await writeFile(join(root, ".thc", "config.json"), JSON.stringify(valid));
    expect((await loadConfig(root)).sandbox).toBe("read-only");
    const path = join(root, "token");
    await writeFile(path, "123:abc\n", { mode: 0o600 });
    expect(await resolveToken({ file: "token" }, root)).toBe("123:abc");
    await chmod(path, 0o644);
    await expect(resolveToken({ file: path }, root)).rejects.toThrow("0600");
    expect(await resolveToken({ env: "TOKEN" }, root, { TOKEN: "1:a" })).toBe(
      "1:a",
    );
    await expect(resolveToken({ env: "TOKEN" }, root, {})).rejects.toThrow(
      "missing or invalid",
    );
    await expect(
      resolveToken({ env: "TOKEN" }, root, { TOKEN: "not-a-token" }),
    ).rejects.toThrow("missing or invalid");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
