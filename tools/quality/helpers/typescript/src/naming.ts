// Names of functions and of the scopes that qualify them.

import type * as ts from "typescript";
import type { Measured, Syntax } from "./syntax.ts";

export const ANONYMOUS = "<anonymous>";

type Named = ts.PropertyName | ts.BindingName;

/** An identifier's text; any other name is spelled as in the source. */
function nameText(syntax: Syntax, name: Named): string {
  const { api } = syntax;
  return api.isIdentifier(name) || api.isPrivateIdentifier(name)
    ? name.text
    : name.getText(syntax.source);
}

function isWrapper(syntax: Syntax, node: ts.Node): boolean {
  const { api } = syntax;
  return (
    api.isParenthesizedExpression(node) ||
    api.isAsExpression(node) ||
    api.isSatisfiesExpression(node) ||
    api.isNonNullExpression(node) ||
    api.isTypeAssertionExpression(node)
  );
}

function isDefaultExport(syntax: Syntax, node: ts.Declaration): boolean {
  const { api } = syntax;
  return (api.getCombinedModifierFlags(node) & api.ModifierFlags.Default) !== 0;
}

/** The name a declaration takes from where it stands, if any. */
function contextualName(
  syntax: Syntax,
  node: ts.Declaration,
): string | undefined {
  const { api } = syntax;
  let value: ts.Node = node;
  while (isWrapper(syntax, value.parent)) {
    value = value.parent;
  }
  const { parent } = value;
  const holder =
    api.isVariableDeclaration(parent) ||
    api.isPropertyAssignment(parent) ||
    api.isPropertyDeclaration(parent);
  if (holder) {
    return nameText(syntax, parent.name);
  }
  return api.isExportAssignment(parent) || isDefaultExport(syntax, node)
    ? "default"
    : undefined;
}

/** The own name of a measured function, without its scopes. */
export function functionName(syntax: Syntax, node: Measured): string {
  const { api } = syntax;
  if (api.isConstructorDeclaration(node)) {
    return "constructor";
  }
  if (
    api.isGetAccessorDeclaration(node) ||
    api.isSetAccessorDeclaration(node)
  ) {
    const prefix = api.isGetAccessorDeclaration(node) ? "get" : "set";
    return `${prefix} ${nameText(syntax, node.name)}`;
  }
  if (node.name !== undefined) {
    return nameText(syntax, node.name);
  }
  return contextualName(syntax, node) ?? ANONYMOUS;
}

/** The scope segment a class or a named object literal adds, if any. */
export function scopeName(syntax: Syntax, node: ts.Node): string | undefined {
  const { api } = syntax;
  if (api.isClassLike(node)) {
    return node.name?.text ?? contextualName(syntax, node) ?? ANONYMOUS;
  }
  return api.isObjectLiteralExpression(node)
    ? contextualName(syntax, node)
    : undefined;
}
