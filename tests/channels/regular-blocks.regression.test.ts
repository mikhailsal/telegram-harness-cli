import { expect, it } from "vitest";
import { renderOutgoing } from "../../src/channels/render.js";

it("keeps paragraph boundaries inside a regular-format blockquote", () => {
  const result = renderOutgoing(
    "> **First paragraph.**\n>\n> Second paragraph.",
    [],
    undefined,
    "regular",
  );
  expect(result.chunks.map((chunk) => chunk.plain).join("\n\n")).toBe(
    "First paragraph.\n\nSecond paragraph.",
  );
});
