import { expect, it } from "vitest";
import { turnDraftId } from "../../src/core/turn-draft.js";

it("derives stable nonzero safe draft IDs from the entire turn ID", () => {
  const createdAt = "2026-10-31T12:00:00.000Z";
  const first = { id: "aabbcc00-0000-4000-8000-000000000001", createdAt };
  const second = { id: "aabbcc00-0000-4000-8000-000000000002", createdAt };
  const firstId = turnDraftId(first);
  const secondId = turnDraftId(second);
  expect(firstId).not.toBe(secondId);
  expect(turnDraftId({ ...first })).toBe(firstId);
  for (const id of [firstId, secondId]) {
    expect(Number.isSafeInteger(id)).toBe(true);
    expect(id).toBeGreaterThan(0);
  }
});
