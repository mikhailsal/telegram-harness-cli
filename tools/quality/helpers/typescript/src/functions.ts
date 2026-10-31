// Every measured function of a source file, with its qualified name.

import type * as ts from "typescript";
import { measure, type FunctionJson } from "./measure.ts";
import { functionName, scopeName } from "./naming.ts";
import { isMeasured, type Syntax } from "./syntax.ts";

/** Order by line, then by name in code point order, as the Python profile does. */
function byPosition(left: FunctionJson, right: FunctionJson): number {
  if (left.line !== right.line) {
    return left.line - right.line;
  }
  return left.name < right.name ? -1 : Number(left.name > right.name);
}

/** Measure each function of the file, ordered by line and name. */
export function measureFunctions(syntax: Syntax): FunctionJson[] {
  const { api } = syntax;
  const found: FunctionJson[] = [];
  const visit = (node: ts.Node, scope: readonly string[]): void => {
    let inner = scope;
    if (isMeasured(api, node)) {
      inner = [...scope, functionName(syntax, node)];
      found.push(measure(syntax, node, inner.join(".")));
    } else {
      const segment = scopeName(syntax, node);
      inner = segment === undefined ? scope : [...scope, segment];
    }
    api.forEachChild(node, (child) => {
      visit(child, inner);
    });
  };
  visit(syntax.source, []);
  return found.sort(byPosition);
}
