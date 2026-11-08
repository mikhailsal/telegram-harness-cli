import { useState } from "react";
import type { ChangeEvent } from "react";
import { flushSync } from "react-dom";

import { EMPTY, FIELDS, send, validate } from "../feedback.ts";
import type { Field as Name, Problems } from "../feedback.ts";
import styles from "./Feedback.module.css";

type Status = "editing" | "sending" | "sent" | "failed";
type Control = HTMLInputElement | HTMLTextAreaElement;

const LABELS: Record<Name, string> = {
  name: "Name",
  email: "Email",
  message: "Message",
};

interface FieldProps {
  name: Name;
  value: string;
  problem: string | undefined;
  onChange: (value: string) => void;
}

/** A labelled control whose problem, when it has one, is its accessible description. */
function Field({ name, value, problem, onChange }: FieldProps) {
  const id = `feedback-${name}`;
  const described = problem === undefined ? undefined : `${id}-problem`;
  const control = {
    id,
    name,
    value,
    "aria-invalid": problem !== undefined,
    "aria-describedby": described,
    onChange: (event: ChangeEvent<Control>) => {
      onChange(event.target.value);
    },
  };
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{LABELS[name]}</label>
      {name === "message" ? (
        <textarea rows={5} {...control} />
      ) : (
        <input
          type={name === "email" ? "email" : "text"}
          autoComplete={name}
          {...control}
        />
      )}
      {described === undefined ? null : (
        <p id={described} className={styles.problem}>
          {problem}
        </p>
      )}
    </div>
  );
}

function progress(status: Status, sender: string): string {
  if (status === "sending") {
    return "Sending your feedback…";
  }
  return status === "sent"
    ? `Thank you, ${sender}. Your feedback was sent.`
    : "";
}

/** The state of the form; a submission while one is being sent is ignored. */
function useFeedback() {
  const [values, setValues] = useState(EMPTY);
  const [problems, setProblems] = useState<Problems>({});
  const [status, setStatus] = useState<Status>("editing");
  const [sender, setSender] = useState("");

  async function submit(form: HTMLFormElement): Promise<void> {
    if (status === "sending") {
      return;
    }
    const found = validate(values);
    // The problems are rendered before the focus moves, so they are announced with it.
    flushSync(() => {
      setProblems(found);
    });
    const first = FIELDS.find((field) => field in found);
    if (first !== undefined) {
      (form.elements.namedItem(first) as Control).focus();
      return;
    }
    setStatus("sending");
    const sent = await send(values);
    if (sent) {
      setSender(values.name.trim());
      setValues(EMPTY);
    }
    setStatus(sent ? "sent" : "failed");
  }

  function change(name: Name, value: string): void {
    setValues((current) => ({ ...current, [name]: value }));
  }

  return { values, problems, status, sender, submit, change };
}

/** The feedback form: each problem takes the focus, and the request shows its progress. */
export function Feedback() {
  const { values, problems, status, sender, submit, change } = useFeedback();
  return (
    <>
      <h1>Send feedback</h1>
      <form
        className={styles.form}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit(event.currentTarget);
        }}
      >
        {FIELDS.map((name) => (
          <Field
            key={name}
            name={name}
            value={values[name]}
            problem={problems[name]}
            onChange={(value) => {
              change(name, value);
            }}
          />
        ))}
        <button type="submit">Send</button>
      </form>
      <p role="status" className={styles.status}>
        {progress(status, sender)}
      </p>
      <p role="alert" className={styles.status}>
        {status === "failed"
          ? "Your feedback could not be sent. Please try again."
          : ""}
      </p>
    </>
  );
}
