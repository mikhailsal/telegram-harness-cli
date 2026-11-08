import { readFile, realpath } from "node:fs/promises";
import { isAbsolute, join, relative } from "node:path";
import { rolloutIdentity } from "./files.ts";
import type { TmuxDescriptor, TmuxOptions } from "./index.ts";
import type { TmuxRunner } from "./runner.ts";

export async function validateDescriptor(
  options: TmuxOptions,
  descriptor: TmuxDescriptor,
  runner: TmuxRunner,
): Promise<void> {
  if (
    !Number.isSafeInteger(descriptor.cursor) ||
    descriptor.cursor < 0 ||
    !descriptor.localId ||
    !Number.isSafeInteger(descriptor.startedAt) ||
    !/^thc-[0-9a-f-]{36}$/u.test(descriptor.originator)
  )
    throw new Error("Invalid session descriptor");
  if (
    descriptor.cwd !== (await realpath(options.cwd)) ||
    descriptor.identity.executable !== (await realpath(options.executable))
  )
    throw new Error("Invalid session descriptor");
  await runner.guard(descriptor.identity);
  await validateProcess(options, descriptor);
  await validateRollout(options, descriptor);
}
async function validateProcess(
  options: TmuxOptions,
  descriptor: TmuxDescriptor,
): Promise<void> {
  if (
    (await realpath(`/proc/${descriptor.identity.pid}/cwd`)) !== descriptor.cwd
  )
    throw new Error("Codex process directory changed");
  const environment = await readFile(
    `/proc/${descriptor.identity.pid}/environ`,
    "utf8",
  );
  if (
    !environment
      .split("\0")
      .includes(`CODEX_INTERNAL_ORIGINATOR_OVERRIDE=${descriptor.originator}`)
  )
    throw new Error("Codex originator mismatch");
  if (!environment.split("\0").includes(`CODEX_HOME=${options.codexHome}`))
    throw new Error("Codex home mismatch");
}
async function validateRollout(
  options: TmuxOptions,
  descriptor: TmuxDescriptor,
): Promise<void> {
  if (!descriptor.rolloutPath && !descriptor.threadId) return;
  const root = await realpath(join(options.codexHome, "sessions"));
  const path = await realpath(descriptor.rolloutPath);
  const within = relative(root, path);
  if (!within || within.startsWith("..") || isAbsolute(within))
    throw new Error("Codex rollout identity mismatch");
  if (
    (await rolloutIdentity(path, descriptor.cwd, descriptor.originator)) !==
    descriptor.threadId
  )
    throw new Error("Codex rollout identity mismatch");
}
