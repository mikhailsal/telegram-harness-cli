import { afterEach, expect, it, vi } from "vitest";
import { parseTime } from "../../src/cli/time.js";
import { CliError } from "../../src/cli/errors.js";

afterEach(() => {
  vi.restoreAllMocks();
});

// Regression: ISSUE-001 — absolute dates and relative ranges bypass validation.
// Found by /qa on 2026-10-31.
// Report: ../telegram-harness-cli/.quality/qa-reports/run-20261031T195454Z/qa-report-thc-2026-10-31.md
it.each([
  "2026-02-30T18:00:00Z",
  "2025-02-29T18:00:00+03:00",
  "999999999999999999999d",
  "999999999999999999999h",
])("rejects invalid time %s with the documented format error", (value) => {
  let failure: unknown;
  try {
    parseTime(value, "UTC");
  } catch (error) {
    failure = error;
  }
  expect(failure).toBeInstanceOf(CliError);
  expect(failure).toMatchObject({ info: { code: "E_TIME_FORMAT" } });
});

it("supports the documented relative hours and preserves relative days", () => {
  vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-31T20:00:00Z"));
  expect(parseTime("12h", "Europe/Moscow")).toBe("2026-10-31T08:00:00.000Z");
  expect(parseTime("7d", "UTC")).toBe("2026-10-24T20:00:00.000Z");
});

it.each([
  ["2024-02-29T18:00:00Z", "2024-02-29T18:00:00.000Z"],
  ["2024-02-29T00:30:00+03:00", "2024-02-28T21:30:00.000Z"],
])("preserves a valid leap day and UTC offset in %s", (input, expected) => {
  expect(parseTime(input, "UTC")).toBe(expected);
});
