import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { formatEntry } from "../../src/cli/history.js";

it("keeps the skill example synchronized with the real history formatter", () => {
  const skill = readFileSync("skills/telegram-history/SKILL.md", "utf8");
  const actual = formatEntry(
    {
      author: "user",
      sentAt: "2026-11-07T10:12:44.000Z",
      text: "We agreed to retry deployment once.",
      possiblyDuplicated: false,
      remainingCharacters: 0,
    },
    "UTC",
  );
  expect(skill).toContain(`\n${actual}\n`);
});
