export interface Message {
  name: string;
  email: string;
  message: string;
}

export type Field = keyof Message;
export type Problems = Partial<Record<Field, string>>;

/** The fields in the order the form shows them. */
export const FIELDS: readonly Field[] = ["name", "email", "message"];
export const EMPTY: Message = { name: "", email: "", message: "" };
export const ENDPOINT = "/api/feedback";
const EMAIL = /^[^\s@]+@[^\s@]+$/;
const SHORTEST = 10;

/** Return what is wrong with each field; an empty object when nothing is. */
export function validate(feedback: Message): Problems {
  const problems: Problems = {};
  if (feedback.name.trim() === "") {
    problems.name = "Enter your name.";
  }
  if (!EMAIL.test(feedback.email.trim())) {
    problems.email = "Enter an email address, such as name@example.com.";
  }
  if (feedback.message.trim().length < SHORTEST) {
    problems.message = `Write a message of at least ${String(SHORTEST)} characters.`;
  }
  return problems;
}

/** Send the feedback to the API; resolve whether the API accepted it. */
export async function send(feedback: Message): Promise<boolean> {
  try {
    const response = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(feedback),
    });
    return response.ok;
  } catch {
    return false;
  }
}
