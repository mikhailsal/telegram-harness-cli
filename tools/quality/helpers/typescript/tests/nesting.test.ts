import { expect, test } from "vitest";
import { functionOf, functionsOf } from "./fixtures.ts";

const CHAIN = `
function chain(n: number) {
  if (n === 1) {
    return "one";
  } else if (n === 2) {
    return "two";
  } else if (n === 3) {
    if (n > 0) {
      return "three";
    }
  } else {
    return "many";
  }
  return "none";
}
`;

test("an else-if chain stays at one level", () => {
  const found = functionOf(CHAIN, "chain");
  expect(found.nesting).toBe(2);
  expect(found.complexity).toBe(5);
});

const ELSE_BLOCK = `
function elseBlock(n: number) {
  if (n === 1) {
    return 1;
  } else {
    if (n === 2) {
      return 2;
    }
  }
  if (n === 3) if (n > 0) return 3;
  return 0;
}
`;

test("an if inside an else block or a then branch adds a level", () => {
  expect(functionOf(ELSE_BLOCK, "elseBlock").nesting).toBe(2);
});

const DEEP = `
function deep(rows: number[][]) {
  let total = 0;
  for (const row of rows) {
    if (row.length > 0) {
      try {
        switch (row[0]) {
          case 1:
            while (total < 1) {
              do {
                total += 1;
              } while (total < 0);
            }
        }
      } catch {
        total = 0;
      }
    }
  }
  for (let i = 0; i < 1; i += 1) {
    for (const key in rows) {
      total += key.length;
    }
  }
  return total;
}
`;

test("loops, switch, and try each add a level", () => {
  expect(functionOf(DEEP, "deep").nesting).toBe(6);
});

const TRY = `
function guarded(run: () => void) {
  try {
    run();
  } catch {
    if (run.length > 0) {
      run();
    }
  } finally {
    {
      label: if (run.name) {
        run();
      }
    }
  }
}
`;

test("catch and finally are at the level of their try", () => {
  expect(functionOf(TRY, "guarded").nesting).toBe(2);
});

const SCOPES = `
function scopes(a: boolean) {
  if (a) {
    const inner = () => {
      if (a) {
        if (a) {
          return 1;
        }
      }
      return 0;
    };
    return inner();
  }
  return class {
    run() {
      if (a) {
        if (a) {
          return 2;
        }
      }
    }
  };
}
`;

test("a nested function starts at zero and does not deepen its parent", () => {
  expect(functionOf(SCOPES, "scopes").nesting).toBe(1);
  expect(functionOf(SCOPES, "scopes.inner").nesting).toBe(2);
  expect(functionOf(SCOPES, "scopes.<anonymous>.run").nesting).toBe(2);
  expect(functionOf("const f = () => () => 1;", "f").nesting).toBe(0);
  expect(functionOf("function e() {}", "e").nesting).toBe(0);
});

const VIEW = `
export function View({ items, on }: { items: string[]; on: boolean }) {
  return (
    <main>
      <section>
        <div>
          <ul>
            {items.map((item) => (
              <li key={item}>
                <span>{on ? <b>{item}</b> : <i>{item}</i>}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </main>
  );
}
`;

test("JSX element depth is not control-flow nesting", () => {
  const [view, item] = functionsOf(VIEW, "view.tsx");
  expect([view?.name, view?.nesting, view?.complexity]).toEqual(["View", 0, 1]);
  expect([item?.name, item?.nesting, item?.complexity]).toEqual([
    "View.<anonymous>",
    0,
    2,
  ]);
});
