import { expect, test } from "vitest";
import { byName, functionOf } from "./fixtures.ts";

const PARAMETERS = `
function receiver(this: Window, a: number, ...rest: string[]) {}
function patterns({ a, b }: { a: number; b: number }, [c]: number[]) {}
class Box {
  constructor(private readonly width: number, height = 2) {}
  set size(value: number) {}
  get size() {
    return 1;
  }
}
`;

test("parameters count rest and patterns once and skip this", () => {
  const found = byName(PARAMETERS);
  expect(found.get("receiver")?.parameters).toBe(2);
  expect(found.get("patterns")?.parameters).toBe(2);
  expect(found.get("Box.constructor")?.parameters).toBe(2);
  expect(found.get("Box.set size")?.parameters).toBe(1);
  expect(found.get("Box.get size")?.parameters).toBe(0);
});

const SIGNATURES = `
function over(a: string): string;
function over(a: number): number;
function over(a: unknown): unknown {
  return a;
}
declare function ambient(): void;
abstract class Shape {
  abstract area(): number;
  method(): void;
  method() {}
}
interface Api {
  call(): void;
  (input: string): void;
}
type Callback = (input: number) => void;
`;

test("only functions with a body are measured", () => {
  const found = byName(SIGNATURES);
  expect([...found.keys()].sort()).toEqual(["Shape.method", "over"]);
  expect(found.get("over")?.line).toBe(4);
});

test("a function starts at its first token, decorators included", () => {
  const text = "class A {\n  @logged\n  run() {}\n}\n";
  expect(functionOf(text, "A.run").line).toBe(2);
});
