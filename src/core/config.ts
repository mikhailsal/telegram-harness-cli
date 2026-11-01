import { readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";
import { z } from "zod";

export const tokenSchema = z.union([
  z.strictObject({ file: z.string().min(1) }),
  z.strictObject({ env: z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/) }),
]);
const seconds = z.number().positive();
const timezone = z.string().refine((value) => {
  try {
    new Intl.DateTimeFormat("en", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}, "Expected an IANA timezone");

export const configSchema = z.strictObject({
  token: tokenSchema,
  allowlist: z.array(z.number().int().positive()).min(1),
  timezone: timezone.default("UTC"),
  status: z.enum(["draft", "message", "off"]).default("draft"),
  mode: z.enum(["queue", "steer"]).default("queue"),
  format: z.enum(["rich", "regular"]).default("rich"),
  mirror_non_tg_input: z.boolean().default(false),
  non_tg_marker: z.string().min(1).max(20).default("🖥"),
  markup: z
    .strictObject({
      tags: z
        .array(z.strictObject({ name: z.string().regex(/^[a-z][a-z0-9-]*$/) }))
        .default([{ name: "internal-note" }]),
    })
    .default({ tags: [{ name: "internal-note" }] }),
  approval_timeout: seconds.nullable().default(null),
  submit_ack_timeout: seconds.default(30),
  stall_timeout: seconds.default(300),
  attach_wait_timeout: seconds.default(120),
  files_keep_days: z.number().int().positive().default(30),
  language: z.enum(["auto", "ru", "en"]).default("auto"),
  update_check: z.boolean().default(true),
  sandbox: z.enum(["read-only", "workspace-write", "danger-full-access"]),
  codex: z
    .strictObject({
      adapter: z.enum(["protocol", "tmux"]).default("protocol"),
      daemon_autostart: z.boolean().default(true),
      executable: z.string().min(1).default("codex"),
      home: z.string().min(1).optional(),
      socket: z.string().min(1).optional(),
    })
    .default({
      adapter: "protocol",
      daemon_autostart: true,
      executable: "codex",
    }),
});

export type Config = z.infer<typeof configSchema>;
export type NoticeCredentials = z.infer<typeof noticeSchema>;
const noticeSchema = configSchema.pick({ token: true, allowlist: true });

export class ConfigurationError extends Error {
  readonly code = "E_CONFIG";
  readonly issues: { path: string; message: string }[];
  constructor(issues: { path: string; message: string }[]) {
    super(
      issues
        .map((issue) => `${JSON.stringify(issue.path)}: ${issue.message}`)
        .join("; "),
    );
    this.name = "ConfigurationError";
    this.issues = issues;
  }
}

export function parseConfig(input: unknown): Config {
  const result = configSchema.safeParse(input);
  if (result.success) return result.data;
  throw new ConfigurationError(
    result.error.issues.map((issue) => ({
      path: issue.path.join(".") || "config",
      message: issue.message,
    })),
  );
}

export function noticeCredentials(
  input: unknown,
): NoticeCredentials | undefined {
  if (typeof input !== "object" || input === null || Array.isArray(input))
    return;
  const values = input as Record<string, unknown>;
  const result = noticeSchema.safeParse({
    token: values.token,
    allowlist: values.allowlist,
  });
  return result.success ? result.data : undefined;
}

export async function readRawConfig(directory: string): Promise<unknown> {
  return JSON.parse(
    await readFile(join(directory, ".thc", "config.json"), "utf8"),
  ) as unknown;
}

export async function loadConfig(directory: string): Promise<Config> {
  return parseConfig(await readRawConfig(directory));
}

export async function resolveToken(
  token: Config["token"],
  directory: string,
  environment: NodeJS.ProcessEnv = process.env,
): Promise<string> {
  let value: string | undefined;
  if ("env" in token) value = environment[token.env];
  else {
    const path = isAbsolute(token.file)
      ? token.file
      : resolve(directory, token.file);
    const metadata = await stat(path);
    if (!metadata.isFile() || (metadata.mode & 0o777) !== 0o600) {
      throw new ConfigurationError([
        { path: "token.file", message: "Expected a private 0600 token file" },
      ]);
    }
    value = (await readFile(path, "utf8")).trim();
  }
  if (!value || !/^\d+:[A-Za-z0-9_-]+$/.test(value)) {
    throw new ConfigurationError([
      { path: "token", message: "Bot token is missing or invalid" },
    ]);
  }
  return value;
}

export function codexHome(config: Config): string {
  return resolve(
    config.codex.home ?? process.env.CODEX_HOME ?? join(homedir(), ".codex"),
  );
}

export function redact(value: string): string {
  return value
    .split(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)/g)
    .map((part) =>
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(part)
        ? part
        : part
            .replace(/\bbot\d+:[A-Za-z0-9_-]+/g, "bot<redacted>")
            .replace(/\b\d+:[A-Za-z0-9_-]+/g, "<redacted>"),
    )
    .join("");
}
