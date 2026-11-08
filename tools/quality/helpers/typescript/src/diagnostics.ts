// Compiler diagnostics in the response format, with 1-based positions.

import { resolve } from "node:path";
import type * as ts from "typescript";
import type { TypeScript } from "./load.ts";

export interface DiagnosticJson {
  readonly file: string | null;
  readonly line: number | null;
  readonly column: number | null;
  readonly code: number;
  readonly category: string;
  readonly message: string;
}

function convert(api: TypeScript, diagnostic: ts.Diagnostic): DiagnosticJson {
  const { file, start } = diagnostic;
  const where =
    file === undefined || start === undefined
      ? undefined
      : file.getLineAndCharacterOfPosition(start);
  return {
    file: file === undefined ? null : resolve(file.fileName),
    line: where === undefined ? null : where.line + 1,
    column: where === undefined ? null : where.character + 1,
    code: diagnostic.code,
    category: api.DiagnosticCategory[diagnostic.category].toLowerCase(),
    message: api.flattenDiagnosticMessageText(diagnostic.messageText, "\n"),
  };
}

/** Convert diagnostics, dropping repeats of the same file, position, code, and message. */
export function convertAll(
  api: TypeScript,
  diagnostics: Iterable<ts.Diagnostic>,
): DiagnosticJson[] {
  const unique = new Map<string, DiagnosticJson>();
  for (const diagnostic of diagnostics) {
    const converted = convert(api, diagnostic);
    const key = JSON.stringify([
      converted.file,
      diagnostic.start,
      converted.code,
      converted.message,
    ]);
    if (!unique.has(key)) {
      unique.set(key, converted);
    }
  }
  return [...unique.values()];
}
