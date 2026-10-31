import { cleanup, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { Feedback } from "../src/pages/Feedback.tsx";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function field(name: string): HTMLElement {
  return screen.getByRole("textbox", { name });
}

async function fill(
  name: string,
  email: string,
  message: string,
): Promise<void> {
  await userEvent.type(field("Name"), name);
  await userEvent.type(field("Email"), email);
  await userEvent.type(field("Message"), message);
}

function respond(): (ok: boolean) => void {
  let answer: (response: Response) => void = () => undefined;
  const response = new Promise<Response>((resolve) => {
    answer = resolve;
  });
  const fetched = vi.fn(() => response);
  vi.stubGlobal("fetch", fetched);
  return (ok) => {
    answer({ ok } as Response);
  };
}

test("an empty form names each problem and focuses the first field", async () => {
  render(<Feedback />);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  const name = screen.getByRole("textbox", {
    name: "Name",
    description: "Enter your name.",
  });
  expect(document.activeElement).toBe(name);
  expect(name.getAttribute("aria-invalid")).toBe("true");
  const email = "Enter an email address, such as name@example.com.";
  expect(
    screen.getByRole("textbox", { name: "Email", description: email }),
  ).toBeDefined();
  const message = "Write a message of at least 10 characters.";
  expect(
    screen.getByRole("textbox", { name: "Message", description: message }),
  ).toBeDefined();
});

test("the first remaining problem takes the focus", async () => {
  render(<Feedback />);
  await userEvent.type(field("Name"), "Ada{Enter}");
  expect(document.activeElement).toBe(field("Email"));
  expect(field("Name").getAttribute("aria-invalid")).toBe("false");
  expect(field("Name").getAttribute("aria-describedby")).toBeNull();
});

test("sent feedback shows its progress, then thanks the sender", async () => {
  const answer = respond();
  render(<Feedback />);
  await fill("Ada", "ada@example.com", "The form is easy to use.");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(screen.getByRole("status").textContent).toBe("Sending your feedback…");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(fetch).toHaveBeenCalledTimes(1);
  answer(true);
  const thanks = "Thank you, Ada. Your feedback was sent.";
  expect((await screen.findByRole("status")).textContent).toBe(thanks);
  expect(field("Name")).toHaveProperty("value", "");
  expect(screen.getByRole("alert").textContent).toBe("");
});

test("a refused request keeps the form and asks to try again", async () => {
  const answer = respond();
  render(<Feedback />);
  await fill("Ada", "ada@example.com", "The form is easy to use.");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  answer(false);
  await screen.findByText("Your feedback could not be sent. Please try again.");
  expect(screen.getByRole("status").textContent).toBe("");
  expect(field("Name")).toHaveProperty("value", "Ada");
});
