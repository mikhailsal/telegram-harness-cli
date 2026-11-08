// The `structure` command: function metrics and resolved imports per file;
// the `imports` command: the resolved imports alone.

import { readFileSync } from "node:fs";
import type * as ts from "typescript";
import { measureFunctions } from "./functions.ts";
import { collectImports, type ImportJson } from "./imports.ts";
import { loadTypeScript, type TypeScript } from "./load.ts";
import type { FunctionJson } from "./measure.ts";
import { canonical, fileOwners, loadProjects } from "./projects.ts";
import {
  absolutePath,
  describe,
  optionalPath,
  pathList,
  type JsonObject,
} from "./protocol.ts";
import { lineOf, syntaxOf } from "./syntax.ts";

export interface FileReport {
  readonly file: string;
  readonly functions: readonly FunctionJson[];
  readonly imports: readonly ImportJson[];
  readonly error: {
    readonly line: number | null;
    readonly message: string;
  } | null;
}

export interface StructureResponse {
  readonly files: readonly FileReport[];
}

export interface ImportsResponse {
  readonly files: readonly Omit<FileReport, "functions">[];
}

type OptionsOf = (file: string) => ts.CompilerOptions;

/** Options per file: its project's, else the root project's, else none. */
function optionsLookup(api: TypeScript, tsconfig: string | null): OptionsOf {
  if (tsconfig === null) {
    return () => ({});
  }
  const graph = loadProjects(api, tsconfig);
  const owners = fileOwners(graph);
  const fallback = graph.root.parsed?.options ?? {};
  return (file) => owners.get(canonical(file))?.options ?? fallback;
}

function parse(
  api: TypeScript,
  file: string,
  options: ts.CompilerOptions,
): ts.SourceFile {
  const text = readFileSync(file, "utf8");
  const kind = file.endsWith(".tsx") ? api.ScriptKind.TSX : api.ScriptKind.TS;
  const impliedNodeFormat = api.getImpliedNodeFormatForFile(
    file,
    undefined,
    api.sys,
    options,
  );
  const settings = {
    languageVersion: api.ScriptTarget.Latest,
    impliedNodeFormat,
  };
  return api.createSourceFile(file, text, settings, true, kind);
}

/** The first syntax error of a parsed file, from a program of that file alone. */
function syntaxError(
  api: TypeScript,
  source: ts.SourceFile,
): ts.DiagnosticWithLocation | undefined {
  const host = api.createCompilerHost({});
  // Without libraries and resolution, the program asks for its root file only.
  host.getSourceFile = () => source;
  const program = api.createProgram({
    rootNames: [source.fileName],
    options: { noLib: true, noResolve: true, types: [] },
    host,
  });
  return program.getSyntacticDiagnostics(source)[0];
}

function analyze(
  api: TypeScript,
  file: string,
  options: ts.CompilerOptions,
): FileReport {
  const source = parse(api, file, options);
  const error = syntaxError(api, source);
  if (error !== undefined) {
    const message = api.flattenDiagnosticMessageText(error.messageText, "\n");
    const line = lineOf(source, error.start);
    return { file, functions: [], imports: [], error: { line, message } };
  }
  const syntax = syntaxOf(api, source);
  return {
    file,
    functions: measureFunctions(syntax),
    imports: collectImports(syntax, options),
    error: null,
  };
}

function report(
  api: TypeScript,
  file: string,
  optionsOf: OptionsOf,
): FileReport {
  try {
    return analyze(api, file, optionsOf(file));
  } catch (error) {
    // An unreadable file, or one too deeply nested for the parser's stack.
    const message = `cannot analyze the file: ${describe(error)}`;
    return { file, functions: [], imports: [], error: { line: null, message } };
  }
}

export function runStructure(request: JsonObject): StructureResponse {
  const api = loadTypeScript(absolutePath(request, "typescript"));
  const tsconfig = optionalPath(request, "tsconfig");
  const files = pathList(request, "files");
  const optionsOf = optionsLookup(api, tsconfig);
  return { files: files.map((file) => report(api, file, optionsOf)) };
}

export function runImports(request: JsonObject): ImportsResponse {
  const { files } = runStructure(request);
  return {
    files: files.map(({ file, imports, error }) => ({ file, imports, error })),
  };
}
