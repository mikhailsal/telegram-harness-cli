// Shared syntax facts for the structure metrics of one source file.

import type * as ts from "typescript";
import type { TypeScript } from "./load.ts";

/** A function with a body: the unit the structure limits apply to. */
export type Measured = ts.FunctionLikeDeclaration & {
  readonly body: ts.ConciseBody;
};

export interface Kinds {
  /** Statements and expressions that add one to the complexity. */
  readonly decisions: ReadonlySet<ts.SyntaxKind>;
  /** Binary operators that add one to the complexity. */
  readonly logical: ReadonlySet<ts.SyntaxKind>;
  /** Statements whose blocks add one level of nesting. */
  readonly nesting: ReadonlySet<ts.SyntaxKind>;
}

export interface Syntax {
  readonly api: TypeScript;
  readonly source: ts.SourceFile;
  readonly kinds: Kinds;
}

export function syntaxOf(api: TypeScript, source: ts.SourceFile): Syntax {
  const kind = api.SyntaxKind;
  const loops = [
    kind.ForStatement,
    kind.ForInStatement,
    kind.ForOfStatement,
    kind.WhileStatement,
    kind.DoStatement,
  ];
  const kinds: Kinds = {
    decisions: new Set([
      kind.IfStatement,
      kind.ConditionalExpression,
      ...loops,
      kind.CatchClause,
      kind.CaseClause,
    ]),
    logical: new Set([
      kind.AmpersandAmpersandToken,
      kind.BarBarToken,
      kind.QuestionQuestionToken,
      kind.AmpersandAmpersandEqualsToken,
      kind.BarBarEqualsToken,
      kind.QuestionQuestionEqualsToken,
    ]),
    nesting: new Set([
      kind.IfStatement,
      ...loops,
      kind.SwitchStatement,
      kind.TryStatement,
    ]),
  };
  return { api, source, kinds };
}

export function isMeasured(api: TypeScript, node: ts.Node): node is Measured {
  return api.isFunctionLike(node) && "body" in node && node.body !== undefined;
}

/** Whether a node opens a scope of its own: a measured function or a class. */
export function isScope(api: TypeScript, node: ts.Node): boolean {
  return isMeasured(api, node) || api.isClassLike(node);
}

/** The 1-based line of a position. */
export function lineOf(source: ts.SourceFile, position: number): number {
  return source.getLineAndCharacterOfPosition(position).line + 1;
}
