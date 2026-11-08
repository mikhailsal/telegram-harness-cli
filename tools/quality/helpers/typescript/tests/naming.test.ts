import { expect, test } from "vitest";
import { namesOf } from "./fixtures.ts";

const NAMES = `
export const arrow = () => 1;
const wrapped = (() => 2) as () => number;
const checked = ((x: number) => x) satisfies (x: number) => number;
const asserted = <() => void>(() => {});
const forced = (() => 3)!;
export class Shape {
  #secret() {}
  "quoted"() {}
  [Symbol.iterator]() {}
  handler = () => {};
  constructor() {}
  get size() {
    return 1;
  }
  set size(value: number) {}
  static make() {
    return function () {};
  }
}
const registry = { open() {}, nested: { close: () => {} } };
const Klass = class {
  run() {}
};
register(class {
  go() {}
});
[1].map(function named() {});
[1].map(() => {});
function outer() {
  function inner() {}
}
const { fallback = () => 4 } = {};
const first = () => 1; const second = () => 2;
`;

test("functions are named by their declaration or their holder", () => {
  expect(namesOf(NAMES)).toEqual([
    "arrow",
    "wrapped",
    "checked",
    "asserted",
    "forced",
    "Shape.#secret",
    'Shape."quoted"',
    "Shape.[Symbol.iterator]",
    "Shape.handler",
    "Shape.constructor",
    "Shape.get size",
    "Shape.set size",
    "Shape.make",
    "Shape.make.<anonymous>",
    "registry.nested.close",
    "registry.open",
    "Klass.run",
    "<anonymous>.go",
    "named",
    "<anonymous>",
    "outer",
    "outer.inner",
    "<anonymous>",
    "first",
    "second",
  ]);
});

test("a default export is named default", () => {
  const declaration = "export default function () {}";
  expect(namesOf(declaration)).toEqual(["default"]);
  const expression = "export default () => 1;";
  expect(namesOf(expression)).toEqual(["default"]);
  const object = "export default { start() {} };";
  expect(namesOf(object)).toEqual(["default.start"]);
  const anonymous = "export default class {\n  run() {}\n}";
  expect(namesOf(anonymous)).toEqual(["default.run"]);
});

test("functions on one line are ordered by name", () => {
  const text = "const zed = () => 1; const alpha = () => 2;";
  expect(namesOf(text)).toEqual(["alpha", "zed"]);
});
