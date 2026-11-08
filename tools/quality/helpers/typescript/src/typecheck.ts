// The `types` command: strict type checking of every project of the graph,
// with referenced projects read from source, so nothing needs to be built.

import { resolve } from "node:path";
import type * as ts from "typescript";
import { convertAll, type DiagnosticJson } from "./diagnostics.ts";
import { loadTypeScript, type TypeScript } from "./load.ts";
import {
  canonical,
  fileOwners,
  loadProjects,
  type Owner,
  type Project,
} from "./projects.ts";
import { absolutePath, type JsonObject } from "./protocol.ts";

/** Flags that `strict` turns on and that a config may still turn off. */
const STRICT_FAMILY = [
  "noImplicitAny",
  "noImplicitThis",
  "strictNullChecks",
  "strictFunctionTypes",
  "strictBindCallApply",
  "strictPropertyInitialization",
  "alwaysStrict",
  "useUnknownInCatchVariables",
  "strictBuiltinIteratorReturn",
] as const;

export interface ProjectReport {
  readonly config: string;
  readonly files: readonly string[];
  readonly problems: readonly string[];
}

export interface TypesResponse {
  readonly projects: readonly ProjectReport[];
  readonly diagnostics: readonly DiagnosticJson[];
}

interface RedirectingHost extends ts.CompilerHost {
  useSourceOfProjectReferenceRedirect: () => boolean;
}

/** The strictness problems of a project's compiler options. */
export function strictness(options: ts.CompilerOptions): string[] {
  const problems = options.strict === true ? [] : ['"strict" is not enabled'];
  for (const flag of STRICT_FAMILY) {
    if (options[flag] === false) {
      problems.push(`"${flag}" is disabled`);
    }
  }
  return problems;
}

function createProgram(
  api: TypeScript,
  parsed: ts.ParsedCommandLine,
): ts.Program {
  const options = {
    ...parsed.options,
    disableSourceOfProjectReferenceRedirect: false,
  };
  // The compiler host hook the language service uses: a referenced project's
  // files come from its sources instead of its built declaration files.
  const host: RedirectingHost = Object.assign(api.createCompilerHost(options), {
    useSourceOfProjectReferenceRedirect: () => true,
  });
  return api.createProgram({
    rootNames: parsed.fileNames,
    options,
    host,
    ...(parsed.projectReferences === undefined
      ? {}
      : { projectReferences: parsed.projectReferences }),
  });
}

function programFiles(program: ts.Program): string[] {
  const files = program
    .getSourceFiles()
    .filter(
      (file) =>
        !program.isSourceFileDefaultLibrary(file) &&
        !program.isSourceFileFromExternalLibrary(file),
    )
    .map((file) => resolve(file.fileName));
  return [...new Set(files)];
}

function check(
  api: TypeScript,
  project: Project,
  owners: ReadonlyMap<string, Owner>,
  found: ts.Diagnostic[],
): ProjectReport {
  const { config, parsed } = project;
  if (parsed === undefined || parsed.fileNames.length === 0) {
    return { config, files: [], problems: [] };
  }
  const program = createProgram(api, parsed);
  // A file that another project lists is checked there, with its own options.
  for (const diagnostic of api.getPreEmitDiagnostics(program)) {
    const { file } = diagnostic;
    const owner =
      file === undefined ? undefined : owners.get(canonical(file.fileName));
    if (owner === undefined || owner.config === config) {
      found.push(diagnostic);
    }
  }
  return {
    config,
    files: programFiles(program),
    problems: strictness(parsed.options),
  };
}

export function runTypes(request: JsonObject): TypesResponse {
  const api = loadTypeScript(absolutePath(request, "typescript"));
  const graph = loadProjects(api, absolutePath(request, "tsconfig"));
  const owners = fileOwners(graph);
  const found = [...graph.diagnostics];
  const projects = graph.projects.map((project) =>
    check(api, project, owners, found),
  );
  return { projects, diagnostics: convertAll(api, found) };
}
