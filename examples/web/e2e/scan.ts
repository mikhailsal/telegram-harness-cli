import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

/** Scan the shown page and attach the raw results, which the quality gate judges. */
export async function scan(page: Page, testInfo: TestInfo): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  await testInfo.attach("accessibility-scan", {
    body: JSON.stringify(results),
    contentType: "application/json",
  });
}
