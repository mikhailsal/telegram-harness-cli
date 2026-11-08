import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { exists } from "./paths.js";

async function appendOnce(
  path: string,
  marker: string,
  content: string,
): Promise<void> {
  let prior = "";
  if (await exists(path)) prior = await readFile(path, "utf8");
  if (!prior.includes(marker))
    await writeFile(
      path,
      `${prior}${prior && !prior.endsWith("\n") ? "\n" : ""}${content}\n`,
    );
}
export async function writeAgentInstructions(
  directory: string,
  root: string,
  force: boolean,
): Promise<void> {
  const skill = join(directory, ".agents", "skills", "thc");
  await mkdir(skill, { recursive: true });
  const source = await readFile(
    join(root, "skills", "telegram-history", "SKILL.md"),
    "utf8",
  );
  const target = join(skill, "SKILL.md");
  const hashPath = join(directory, ".thc", "skill.sha256");
  const hash = (text: string): string =>
    createHash("sha256").update(text).digest("hex");
  const recorded = (await exists(hashPath))
    ? (await readFile(hashPath, "utf8")).trim()
    : undefined;
  const old = (await exists(target))
    ? await readFile(target, "utf8")
    : undefined;
  if (
    old === undefined ||
    force ||
    (recorded !== undefined && hash(old) === recorded)
  ) {
    await writeFile(target, source, { mode: 0o600 });
    await writeFile(hashPath, `${hash(source)}\n`, { mode: 0o600 });
  }
  await appendOnce(
    join(directory, "AGENTS.md"),
    "<!-- thc:begin -->",
    "<!-- thc:begin -->\nTelegram chat history is available through the thc skill. Use thc search when earlier chat context is needed.\n<!-- thc:end -->",
  );
}
