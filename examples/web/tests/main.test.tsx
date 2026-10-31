import { act, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

// The entry point runs on import, so each test imports a fresh copy.
beforeEach(() => {
  vi.resetModules();
  window.history.replaceState(null, "", "/");
});

test("the page renders the application into its root", async () => {
  document.body.innerHTML = '<div id="root"></div>';
  await act(async () => {
    await import("../src/main.tsx");
  });
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Welcome");
});

test("a page without a root element does not start", async () => {
  document.body.innerHTML = "";
  await expect(import("../src/main.tsx")).rejects.toThrow(
    "the page has no #root element",
  );
});
