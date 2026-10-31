import { join } from "node:path";
import { expect, test } from "vitest";
import { functionOf, project, structureOf } from "./fixtures.ts";

const COMMENTS = `function lines() {
  // a comment

  /* a block
     comment */
  /** A documented constant. */
  const text = \`first
\${"middle"}

last\`;
  return text; // trailing
}
`;

test("comments and blank lines do not count; a template counts every line", () => {
  // Lines 7 to 11: the template spans 7 to 10, and the return is on 11.
  expect(functionOf(COMMENTS, "lines").code_lines).toBe(5);
});

const NESTED = `function outer() {
  const inner = () => {

    return 1;
  };
  return inner;
}
`;

test("a nested function's lines count towards the enclosing body", () => {
  expect(functionOf(NESTED, "outer").code_lines).toBe(4);
  expect(functionOf(NESTED, "outer.inner").code_lines).toBe(1);
});

test("braces are not part of a block body", () => {
  expect(functionOf("function empty() {}", "empty").code_lines).toBe(0);
  expect(functionOf("function one() { return 1; }", "one").code_lines).toBe(1);
  const text = "function wide() {\n  return [\n    1,\n\n    2,\n  ];\n}\n";
  expect(functionOf(text, "wide").code_lines).toBe(4);
});

test("an expression body counts the lines of its expression", () => {
  const text = "const add = (a: number, b: number) =>\n  a +\n  b;\n";
  expect(functionOf(text, "add").code_lines).toBe(2);
});

const VIEW = `export function View(props: { name: string }) {
  return (
    <div>

      hello {props.name}

      <p>
        alpha

        beta
      </p>
    </div>
  );
}
`;

test("JSX text counts its lines with text only", () => {
  const root = project({ "view.tsx": VIEW });
  const report = structureOf(join(root, "view.tsx"), null);
  expect(report.error).toBeNull();
  // Lines 2, 3, 5, 7, 8, 10, 11, 12, and 13.
  expect(report.functions[0]?.code_lines).toBe(9);
});
