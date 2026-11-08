// Module specifiers of a source file, the files they resolve to, and whether
// they name a Node builtin or are erased as type-only imports.

import { isBuiltin } from "node:module";
import { resolve } from "node:path";
import type * as ts from "typescript";
import { lineOf, type Syntax } from "./syntax.ts";

export interface ImportJson {
  readonly specifier: string;
  readonly line: number;
  readonly resolved: string | null;
  readonly builtin: boolean;
  readonly typeOnly: boolean;
}

interface Found {
  readonly node: ts.Node;
  readonly specifier: ts.StringLiteralLike;
}

function literal(
  syntax: Syntax,
  node: ts.Node | undefined,
): ts.StringLiteralLike | undefined {
  return node !== undefined && syntax.api.isStringLiteralLike(node)
    ? node
    : undefined;
}

function isLoader(syntax: Syntax, callee: ts.Expression): boolean {
  const { api } = syntax;
  return (
    callee.kind === api.SyntaxKind.ImportKeyword ||
    (api.isIdentifier(callee) && callee.text === "require")
  );
}

/** The specifier of an import, an export from a module, or a loader call. */
function specifierOf(
  syntax: Syntax,
  node: ts.Node,
): ts.StringLiteralLike | undefined {
  const { api } = syntax;
  if (api.isImportDeclaration(node) || api.isExportDeclaration(node)) {
    return literal(syntax, node.moduleSpecifier);
  }
  if (api.isExternalModuleReference(node)) {
    return literal(syntax, node.expression);
  }
  return api.isCallExpression(node) && isLoader(syntax, node.expression)
    ? literal(syntax, node.arguments[0])
    : undefined;
}

/** Whether the compiler erases the import: `import type` and `export type`. */
function isTypeOnly(syntax: Syntax, node: ts.Node): boolean {
  const { api } = syntax;
  if (api.isImportDeclaration(node)) {
    return node.importClause?.phaseModifier === api.SyntaxKind.TypeKeyword;
  }
  if (api.isExportDeclaration(node)) {
    return node.isTypeOnly;
  }
  return api.isExternalModuleReference(node) && node.parent.isTypeOnly;
}

function resolveSpecifier(
  syntax: Syntax,
  specifier: ts.StringLiteralLike,
  options: ts.CompilerOptions,
): string | null {
  const { api, source } = syntax;
  const mode = api.getModeForUsageLocation(source, specifier, options);
  const { resolvedModule } = api.resolveModuleName(
    specifier.text,
    source.fileName,
    options,
    api.sys,
    undefined,
    undefined,
    mode,
  );
  return resolvedModule === undefined ||
    resolvedModule.isExternalLibraryImport === true
    ? null
    : resolve(resolvedModule.resolvedFileName);
}

/** Every module specifier of the file in source order, resolved with `options`. */
export function collectImports(
  syntax: Syntax,
  options: ts.CompilerOptions,
): ImportJson[] {
  const found: Found[] = [];
  const visit = (node: ts.Node): void => {
    const specifier = specifierOf(syntax, node);
    if (specifier !== undefined) {
      found.push({ node, specifier });
    }
    syntax.api.forEachChild(node, visit);
  };
  visit(syntax.source);
  return found.map(({ node, specifier }) => ({
    specifier: specifier.text,
    line: lineOf(syntax.source, specifier.getStart(syntax.source)),
    resolved: resolveSpecifier(syntax, specifier, options),
    builtin: isBuiltin(specifier.text),
    typeOnly: isTypeOnly(syntax, node),
  }));
}
