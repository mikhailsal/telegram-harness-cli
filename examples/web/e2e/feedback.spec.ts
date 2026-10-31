import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { scan } from "./scan.ts";

// The feedback API is simulated by a route in the browser; no backend runs.
const API = "**/api/feedback";
const MESSAGE = "The new form is easy to use.";

async function open(page: Page): Promise<void> {
  await page.goto("/feedback");
  await expect(
    page.getByRole("heading", { level: 1, name: "Send feedback" }),
  ).toBeVisible();
}

async function fill(page: Page): Promise<void> {
  await page.getByRole("textbox", { name: "Name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Email" }).fill("ada@example.com");
  await page.getByRole("textbox", { name: "Message" }).fill(MESSAGE);
}

test("an incomplete form is corrected and sent from the keyboard", async ({
  page,
}, testInfo) => {
  await page.route(API, (route) =>
    route.fulfill({ status: 201, json: { id: 1 } }),
  );
  await open(page);
  const name = page.getByRole("textbox", { name: "Name" });
  const email = page.getByRole("textbox", { name: "Email" });
  const send = page.getByRole("button", { name: "Send" });
  const order = [
    page.getByRole("link", { name: "Home" }),
    page.getByRole("link", { name: "Feedback", exact: true }),
    name,
    email,
    page.getByRole("textbox", { name: "Message" }),
    send,
  ];
  for (const control of order) {
    await page.keyboard.press("Tab");
    await expect(control).toBeFocused();
  }
  await page.keyboard.press("Enter");
  await expect(name).toBeFocused();
  await expect(name).toHaveAttribute("aria-invalid", "true");
  await expect(name).toHaveAccessibleDescription("Enter your name.");
  await scan(page, testInfo);
  await page.keyboard.type("Ada");
  await page.keyboard.press("Enter");
  await expect(email).toBeFocused();
  await page.keyboard.type("ada@example.com");
  await page.keyboard.press("Tab");
  await page.keyboard.type(MESSAGE);
  await page.keyboard.press("Tab");
  await expect(send).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toHaveText(
    "Thank you, Ada. Your feedback was sent.",
  );
});

test("sent feedback shows its progress and then thanks the sender", async ({
  page,
}, testInfo) => {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const sent: unknown[] = [];
  await page.route(API, async (route) => {
    sent.push(route.request().postDataJSON());
    await held;
    await route.fulfill({ status: 201, json: { id: 1 } });
  });
  await open(page);
  await fill(page);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("status")).toHaveText("Sending your feedback…");
  release();
  await expect(page.getByRole("status")).toHaveText(
    "Thank you, Ada. Your feedback was sent.",
  );
  await expect(page.getByRole("textbox", { name: "Name" })).toHaveValue("");
  expect(sent).toEqual([
    { name: "Ada", email: "ada@example.com", message: MESSAGE },
  ]);
  await scan(page, testInfo);
});

test("a refused request keeps the form and asks to try again", async ({
  page,
}, testInfo) => {
  await page.route(API, (route) =>
    route.fulfill({ status: 503, json: { error: "down" } }),
  );
  await open(page);
  await fill(page);
  await page.getByRole("button", { name: "Send" }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toHaveText(
    "Your feedback could not be sent. Please try again.",
  );
  await expect(page.getByRole("textbox", { name: "Name" })).toHaveValue("Ada");
  await scan(page, testInfo);
});
