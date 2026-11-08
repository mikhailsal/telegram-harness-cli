import { afterEach, expect, test, vi } from "vitest";

import { EMPTY, ENDPOINT, send, validate } from "../src/feedback.ts";

const VALID = {
  name: "Ada",
  email: "ada@example.com",
  message: "The form is easy to use.",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

test("valid feedback has no problems", () => {
  expect(validate(VALID)).toEqual({});
});

test("each field names its own problem", () => {
  expect(validate(EMPTY)).toEqual({
    name: "Enter your name.",
    email: "Enter an email address, such as name@example.com.",
    message: "Write a message of at least 10 characters.",
  });
  const blank = { name: "  ", email: "ada@", message: " too short " };
  expect(Object.keys(validate(blank))).toEqual(["name", "email", "message"]);
});

test("feedback is posted to the API as JSON", async () => {
  const fetched = vi.fn(() => Promise.resolve({ ok: true } as Response));
  vi.stubGlobal("fetch", fetched);
  await expect(send(VALID)).resolves.toBe(true);
  expect(fetched).toHaveBeenCalledWith(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(VALID),
  });
});

test("a refused or failed request is not sent", async () => {
  vi.stubGlobal("fetch", () => Promise.resolve({ ok: false } as Response));
  await expect(send(VALID)).resolves.toBe(false);
  vi.stubGlobal("fetch", () => Promise.reject(new TypeError("offline")));
  await expect(send(VALID)).resolves.toBe(false);
});
