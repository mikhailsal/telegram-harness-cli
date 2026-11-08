import { act, cleanup, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test } from "vitest";

import { App } from "../src/App.tsx";

function heading(): string | null {
  return screen.getByRole("heading", { level: 1 }).textContent;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
});

test("the home page marks its link as the current page", () => {
  render(<App />);
  expect(heading()).toBe("Welcome");
  expect(document.title).toBe("Home · Feedback desk");
  const navigation = screen.getByRole("navigation", { name: "Main" });
  expect(navigation.querySelector("[aria-current=page]")?.textContent).toBe(
    "Home",
  );
});

test("a link shows its page without loading the document", async () => {
  render(<App />);
  await userEvent.click(screen.getByRole("link", { name: "Send feedback" }));
  expect(window.location.pathname).toBe("/feedback");
  expect(heading()).toBe("Send feedback");
  expect(document.title).toBe("Send feedback · Feedback desk");
  const current = screen.getByRole("link", { name: "Feedback" });
  expect(current.getAttribute("aria-current")).toBe("page");
});

test("the browser's history shows the earlier page again", () => {
  render(<App />);
  act(() => {
    window.history.pushState(null, "", "/feedback");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(heading()).toBe("Send feedback");
  act(() => {
    window.history.replaceState(null, "", "/");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(heading()).toBe("Welcome");
});

test("an unknown address says so and leads home", async () => {
  window.history.replaceState(null, "", "/no-such-page");
  render(<App />);
  expect(heading()).toBe("Page not found");
  expect(document.title).toBe("Page not found · Feedback desk");
  await userEvent.click(
    screen.getByRole("link", { name: "Go to the home page" }),
  );
  expect(heading()).toBe("Welcome");
});
