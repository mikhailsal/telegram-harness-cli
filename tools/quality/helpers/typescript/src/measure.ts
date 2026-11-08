// The metrics of one function, following the counting rules of decision 0007.

import type * as ts from "typescript";
import { isScope, lineOf, type Measured, type Syntax } from "./syntax.ts";

export interface FunctionJson {
  readonly name: string;
  readonly line: number;
  readonly code_lines: number;
  readonly complexity: number;
  readonly nesting: number;
  readonly parameters: number;
}

function isJsDoc(syntax: Syntax, node: ts.Node): boolean {
  const kind = syntax.api.SyntaxKind;
  return node.kind >= kind.FirstJSDocNode && node.kind <= kind.LastJSDocNode;
}

/** Add the lines that a token covers; JSX text counts its non-blank lines. */
function markToken(syntax: Syntax, token: ts.Node, lines: Set<number>): void {
  const { api, source } = syntax;
  const start = token.getStart(source);
  const parts = source.text.slice(start, token.getEnd()).split("\n");
  const first = lineOf(source, start);
  parts.forEach((part, offset) => {
    if (!api.isJsxText(token) || /\S/.test(part)) {
      lines.add(first + offset);
    }
  });
}

function markTokens(syntax: Syntax, node: ts.Node, lines: Set<number>): void {
  if (isJsDoc(syntax, node)) {
    return;
  }
  const children = node.getChildren(syntax.source);
  if (children.length === 0 && node.getWidth(syntax.source) > 0) {
    markToken(syntax, node, lines);
  }
  for (const child of children) {
    markTokens(syntax, child, lines);
  }
}

/** Lines holding a token of the body: between the braces of a block body. */
export function codeLines(syntax: Syntax, body: ts.ConciseBody): number {
  const lines = new Set<number>();
  const roots = syntax.api.isBlock(body) ? body.statements : [body];
  for (const root of roots) {
    markTokens(syntax, root, lines);
  }
  return lines.size;
}

function isDecision(syntax: Syntax, node: ts.Node): boolean {
  const { api, kinds } = syntax;
  if (api.isBinaryExpression(node)) {
    return kinds.logical.has(node.operatorToken.kind);
  }
  return kinds.decisions.has(node.kind);
}

/** One plus the decisions of the body, outside nested functions and classes. */
export function complexity(syntax: Syntax, body: ts.ConciseBody): number {
  let total = 1;
  const visit = (node: ts.Node): void => {
    if (isScope(syntax.api, node)) {
      return;
    }
    total += isDecision(syntax, node) ? 1 : 0;
    syntax.api.forEachChild(node, visit);
  };
  visit(body);
  return total;
}

function isElseIf(syntax: Syntax, node: ts.Node): boolean {
  const { api } = syntax;
  const { parent } = node;
  return (
    api.isIfStatement(node) &&
    api.isIfStatement(parent) &&
    parent.elseStatement === node
  );
}

function depth(syntax: Syntax, node: ts.Node, level: number): number {
  if (isScope(syntax.api, node)) {
    return level;
  }
  const opens = syntax.kinds.nesting.has(node.kind) && !isElseIf(syntax, node);
  const inner = opens ? level + 1 : level;
  let deepest = inner;
  syntax.api.forEachChild(node, (child) => {
    deepest = Math.max(deepest, depth(syntax, child, inner));
  });
  return deepest;
}

/** The deepest chain of nesting statements; an `else if` stays at its level. */
export function nesting(syntax: Syntax, body: ts.ConciseBody): number {
  return depth(syntax, body, 0);
}

/** Declared parameters, without a TypeScript `this` parameter. */
export function parameters(syntax: Syntax, node: Measured): number {
  const { api } = syntax;
  return node.parameters.filter(
    (parameter) =>
      !(api.isIdentifier(parameter.name) && parameter.name.text === "this"),
  ).length;
}

export function measure(
  syntax: Syntax,
  node: Measured,
  name: string,
): FunctionJson {
  return {
    name,
    line: lineOf(syntax.source, node.getStart(syntax.source)),
    code_lines: codeLines(syntax, node.body),
    complexity: complexity(syntax, node.body),
    nesting: nesting(syntax, node.body),
    parameters: parameters(syntax, node),
  };
}
