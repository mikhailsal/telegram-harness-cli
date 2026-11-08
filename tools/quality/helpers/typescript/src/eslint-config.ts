// The `eslint-config` command: whether ESLint configures each file, whether
// that configuration lints it with type information, and the severity it
// gives each requested rule.

import type { ESLint } from "eslint";
import { loadEslint } from "./load.ts";
import {
  absolutePath,
  field,
  optionalStrings,
  pathList,
  type JsonObject,
} from "./protocol.ts";

/** 0 for a rule that is off or not configured, 1 for a warning, 2 for an error. */
export interface RuleSeverity {
  readonly rule: string;
  readonly severity: number;
}

export interface FileConfig {
  readonly file: string;
  readonly configured: boolean;
  readonly ignored: boolean;
  readonly typed: boolean;
  readonly parser: string | null;
  readonly rules: readonly RuleSeverity[];
}

export interface EslintConfigResponse {
  readonly files: readonly FileConfig[];
}

/** ESLint normalizes each rule entry to a list that starts with its numeric severity. */
function severityOf(entry: unknown): number {
  const level: unknown = Array.isArray(entry) ? entry[0] : undefined;
  return typeof level === "number" ? level : 0;
}

async function inspect(
  eslint: ESLint,
  file: string,
  rules: readonly string[],
): Promise<FileConfig> {
  const config: unknown = await eslint.calculateConfigForFile(file);
  const configured = field(config, "rules");
  const language = field(config, "languageOptions");
  const options = field(language, "parserOptions");
  const name = field(field(field(language, "parser"), "meta"), "name");
  const typed =
    Boolean(field(options, "projectService")) ||
    Boolean(field(options, "project"));
  return {
    file,
    configured: config !== undefined,
    ignored: await eslint.isPathIgnored(file),
    typed,
    parser: typeof name === "string" ? name : null,
    rules: rules.map((rule) => ({
      rule,
      severity: severityOf(field(configured, rule)),
    })),
  };
}

export async function runEslintConfig(
  request: JsonObject,
): Promise<EslintConfigResponse> {
  const ESLintClass = await loadEslint(absolutePath(request, "eslint"));
  const eslint = new ESLintClass({ cwd: absolutePath(request, "cwd") });
  const rules = optionalStrings(request, "rules");
  const files: FileConfig[] = [];
  for (const file of pathList(request, "files")) {
    files.push(await inspect(eslint, file, rules));
  }
  return { files };
}
