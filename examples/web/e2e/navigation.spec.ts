import { expect, test } from "@playwright/test";

import { scan } from "./scan.ts";

test("the home page leads to the feedback form and back", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: "Welcome" }),
  ).toBeVisible();
  const home = page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Home" });
  await expect(home).toHaveAttribute("aria-current", "page");
  await scan(page, testInfo);
  await page.getByRole("link", { name: "Send feedback" }).click();
  await expect(page).toHaveURL("/feedback");
  await expect(
    page.getByRole("heading", { level: 1, name: "Send feedback" }),
  ).toBeVisible();
  await expect(page).toHaveTitle("Send feedback · Feedback desk");
  await page.goBack();
  await expect(
    page.getByRole("heading", { level: 1, name: "Welcome" }),
  ).toBeVisible();
});

test("a deep link opens its page, and an unknown address leads home", async ({
  page,
}) => {
  await page.goto("/feedback");
  await expect(
    page.getByRole("heading", { level: 1, name: "Send feedback" }),
  ).toBeVisible();
  await page.goto("/no-such-page");
  await expect(
    page.getByRole("heading", { level: 1, name: "Page not found" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Go to the home page" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Welcome" }),
  ).toBeVisible();
});
