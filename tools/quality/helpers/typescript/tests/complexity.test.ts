import { expect, test } from "vitest";
import { functionOf } from "./fixtures.ts";

const DECISIONS = `
export function decide(a: boolean, b: boolean, items: number[], text?: string) {
  let total = 0;
  if (a) total += 1;
  for (let i = 0; i < 2; i += 1) total += i;
  for (const key in { x: 1 }) total += key.length;
  for (const item of items) total += item;
  while (total > 100) total -= 1;
  do {
    total += 1;
  } while (total < 0);
  try {
    total += 1;
  } catch {
    total -= 1;
  } finally {
    total += 0;
  }
  switch (total) {
    case 1:
      total += 1;
      break;
    case 2:
      break;
    default:
      break;
  }
  total += a && b ? 1 : 0;
  total += a || b ? 1 : 0;
  total += text?.length ?? 0;
  let flag: boolean | undefined;
  flag ||= a;
  flag &&= b;
  flag ??= true;
  return flag ? total : -total;
}
`;

test("each decision kind adds one", () => {
  // 1 + if, for, for-in, for-of, while, do, catch, two cases, && and ?:,
  // || and ?:, ??, ||=, &&=, ??=, and the final ?: = 19.
  expect(functionOf(DECISIONS, "decide").complexity).toBe(19);
});

const NEUTRAL = `
function neutral(value = 1, items?: number[]) {
  label: {
    break label;
  }
  const length = items?.length;
  const sum = value + (length ?? 0) - 1;
  return sum;
}
`;

test("defaults, labels, and optional chaining add nothing", () => {
  // Only the ?? adds one; the default value is not part of the body.
  expect(functionOf(NEUTRAL, "neutral").complexity).toBe(2);
});

const NESTED = `
function outer(a: boolean, b: boolean) {
  if (a) {
    const inner = () => (b ? 1 : 2);
    return inner();
  }
  class Local {
    pick() {
      return a && b;
    }
    value = a ? 1 : 2;
  }
  return new Local().pick();
}
`;

test("nested functions and classes keep their decisions", () => {
  expect(functionOf(NESTED, "outer").complexity).toBe(2);
  expect(functionOf(NESTED, "outer.inner").complexity).toBe(2);
  expect(functionOf(NESTED, "outer.Local.pick").complexity).toBe(2);
});

test("an expression body counts its own decisions", () => {
  const text = "const pick = (a: boolean) => (a ? 1 : a === false ? 2 : 3);";
  expect(functionOf(text, "pick").complexity).toBe(3);
  const curried = "const make = (a: number) => (b: number) => a || b;";
  expect(functionOf(curried, "make").complexity).toBe(1);
  expect(functionOf(curried, "make.<anonymous>").complexity).toBe(2);
});
