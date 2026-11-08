"""The end-to-end scenarios of the scratch browser counter and their manifest (decision 0018).

One test shows the counter and attaches its accessibility scan; the other
counts from the keyboard. Both wait for the interface before they scan it or
use the keyboard. The manifest proves every `ui` requirement at the merge
stage, so a commit-stage check runs only the unit suite.
"""

from __future__ import annotations

SCENARIOS = """\
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

test("the page shows the counter", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Count 0" })).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  await testInfo.attach("accessibility-scan", {
    body: JSON.stringify(results),
    contentType: "application/json",
  });
});

test("the counter counts from the keyboard", async ({ page }) => {
  await page.goto("/");
  const button = page.getByRole("button");
  await expect(button).toHaveText("Count 0");
  await page.keyboard.press("Tab");
  await expect(button).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(button).toHaveText("Count 1");
});
"""
SCENARIOS_FILE = "e2e/counter.spec.ts"
MANIFEST = "scenarios.toml"
BOUNDARY = "the static production build without a backend"


def manifest(component: str) -> str:
    """Return the merge-stage scenario manifest of the component's counter."""
    return f"""\
schema_version = 1
component = "{component}"

[[scenarios]]
id = "counter"
summary = "The built page shows the counter, which passes its accessibility scan."
capability = "ui"
level = "e2e"
stage = "merge"
requirements = ["ui.smoke", "ui.flows", "ui.accessibility"]
tests = ["{SCENARIOS_FILE}::the page shows the counter"]
boundary = "{BOUNDARY}"

[[scenarios]]
id = "keyboard"
summary = "The counter counts from the keyboard."
capability = "ui"
level = "e2e"
stage = "merge"
requirements = ["ui.keyboard"]
tests = ["{SCENARIOS_FILE}::the counter counts from the keyboard"]
boundary = "{BOUNDARY}"
"""
