// The project graph of a component: its root tsconfig and every project the
// root references, directly or through other references.

import { realpathSync } from "node:fs";
import { resolve } from "node:path";
import type * as ts from "typescript";
import type { TypeScript } from "./load.ts";

export interface Project {
  readonly config: string;
  /** The parsed configuration, or undefined when the file cannot be read. */
  readonly parsed: ts.ParsedCommandLine | undefined;
}

export interface ProjectGraph {
  readonly root: Project;
  readonly projects: readonly Project[];
  readonly diagnostics: readonly ts.Diagnostic[];
}

/** The real path of a file, or its absolute path when it does not exist. */
export function canonical(path: string): string {
  try {
    return realpathSync(path);
  } catch {
    return resolve(path);
  }
}

function parse(
  api: TypeScript,
  config: string,
  diagnostics: ts.Diagnostic[],
): ts.ParsedCommandLine | undefined {
  const host: ts.ParseConfigFileHost = {
    ...api.sys,
    onUnRecoverableConfigFileDiagnostic: (diagnostic) => {
      diagnostics.push(diagnostic);
    },
  };
  const parsed = api.getParsedCommandLineOfConfigFile(config, undefined, host);
  diagnostics.push(...(parsed?.errors ?? []));
  return parsed;
}

/** Parse the root config and, breadth first, every config it references. */
export function loadProjects(api: TypeScript, root: string): ProjectGraph {
  const diagnostics: ts.Diagnostic[] = [];
  const visit = (config: string): Project => ({
    config,
    parsed: parse(api, config, diagnostics),
  });
  const first = visit(resolve(root));
  const projects = [first];
  const seen = new Set([canonical(first.config)]);
  // The loop also visits the projects appended to `projects` while it runs.
  for (const project of projects) {
    for (const reference of project.parsed?.projectReferences ?? []) {
      const config = api.resolveProjectReferencePath(reference);
      if (!seen.has(canonical(config))) {
        seen.add(canonical(config));
        projects.push(visit(config));
      }
    }
  }
  return { root: first, projects, diagnostics };
}

/** The project that lists a file, with its compiler options. */
export interface Owner {
  readonly config: string;
  readonly options: ts.CompilerOptions;
}

/** Map the real path of each file of the graph to the first project that lists it. */
export function fileOwners(graph: ProjectGraph): ReadonlyMap<string, Owner> {
  const listed = graph.projects.flatMap(({ config, parsed }) =>
    parsed === undefined
      ? []
      : parsed.fileNames.map((file) => ({
          file: canonical(file),
          owner: { config, options: parsed.options },
        })),
  );
  const owners = new Map<string, Owner>();
  for (const { file, owner } of listed) {
    if (!owners.has(file)) {
      owners.set(file, owner);
    }
  }
  return owners;
}
