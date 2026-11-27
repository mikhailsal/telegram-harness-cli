import { expect, it, vi } from "vitest";
import type { AdapterEvent } from "../../src/adapters/contract.js";
import type { ChannelEvent, InlineButton } from "../../src/channels/index.js";
import { message, setup } from "./continuation-fixture.js";

const questions = [
  { title: "Cats or dogs?", options: ["cats", "dogs"] },
  { title: "Which name?", options: null },
];
function asked(
  data: unknown = { delivery: "async", itemId: "call_1", questions },
) {
  return {
    kind: "output",
    sessionId: "thread",
    turnId: "turn-1",
    cursor: "turn-1/item/call_1",
    text: "Cats or dogs?\n- cats\n- dogs\n\nWhich name?",
    data,
  } satisfies AdapterEvent;
}
function tap(updateId: number, data: string, messageId = 100): ChannelEvent {
  return {
    kind: "decision",
    updateId,
    chatId: "1",
    userId: "1",
    messageId,
    sentAt: "2026-11-15T07:36:34.000Z",
    language: "en",
    callbackId: `cb-${String(updateId)}`,
    data,
  };
}
const reply = [
  "<send_user_message_question_reply>",
  '[{"answer":"dogs","question":"Cats or dogs?","questionItemId":"[\\"request_user_input_async\\",\\"call_1\\",0]"}]',
  "</send_user_message_question_reply>",
].join("\n");

function buttonSetup(mode: "queue" | "steer" = "queue") {
  const fixture = setup({ mode });
  const adapter = fixture.context.adapter as unknown as {
    capabilities: Set<string>;
    steer: ReturnType<typeof vi.fn>;
    submit: ReturnType<typeof vi.fn>;
  };
  adapter.capabilities.add("steer");
  adapter.steer = vi.fn().mockResolvedValue(undefined);
  const api = fixture.context.channel.bot.api as unknown as Record<
    string,
    unknown
  >;
  const edit = vi.fn().mockResolvedValue(true);
  api.editMessageReplyMarkup = edit;
  const { answerCallback } = fixture.context.channel as unknown as {
    answerCallback: ReturnType<typeof vi.fn>;
  };
  return { ...fixture, adapter, edit, answerCallback };
}
function keyboard(
  deliverChunk: ReturnType<typeof setup>["deliverChunk"],
  call: number,
): InlineButton[][] | undefined {
  const options = deliverChunk.mock.calls[call]?.[2] as
    { keyboard?: InlineButton[][] } | undefined;
  return options?.keyboard;
}

// Value: protects=each question of a request_user_input_async item is its own message, options as buttons; fails_when=the item text is sent whole or the buttons never reach the outbox; why_new=questions arrived as text only; seam=deliverChunk; no_claim=Telegram rendering
it("sends each question as a message whose options are buttons", async () => {
  const { engine, store, deliverChunk } = buttonSetup();
  try {
    await engine.accept([message(7, "ask me")], 8);
    await engine.event(asked());
    // protects=the first question shows its title with one button per option, the free-form one plain text; fails_when=options are listed as text or a button lacks its callback; no_claim=none beyond protects
    expect(deliverChunk.mock.calls.map((call) => call[1].plain)).toEqual([
      "Cats or dogs?",
      "Which name?",
    ]);
    const buttons = keyboard(deliverChunk, 0);
    expect(buttons?.map((row) => row.map((button) => button.text))).toEqual([
      ["cats"],
      ["dogs"],
    ]);
    expect(buttons?.[1]?.[0]?.callback_data).toMatch(/^aq:[0-9a-f]{16}:1$/u);
    expect(keyboard(deliverChunk, 1)).toBeUndefined();
    await engine.event(asked());
    // protects=a replayed item is not sent again; fails_when=reconnect history resends the questions; no_claim=none beyond protects
    expect(deliverChunk).toHaveBeenCalledTimes(2);
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=a tap answers inside the running task in Queue mode, in the format Codex's own client uses, once; fails_when=the answer waits for the task to end, is wrapped in the message envelope, or a second tap answers again; why_new=buttons are new; seam=adapter.steer; no_claim=how Codex reacts to the reply
it("steers a tapped answer into the running task, once", async () => {
  const { engine, store, deliverChunk, adapter, edit, answerCallback } =
    buttonSetup("queue");
  try {
    await engine.accept([message(7, "ask me")], 8);
    await engine.event(asked());
    store.setCursor("next-reaction", "👍 to answer");
    const data = keyboard(deliverChunk, 0)?.[1]?.[0]?.callback_data ?? "";
    await engine.accept([tap(9, data)], 10);
    // protects=the answer is steered at once with the reply block alone; fails_when=Queue mode holds it or reactions are folded in; no_claim=none beyond protects
    expect(adapter.steer).toHaveBeenCalledTimes(1);
    expect(adapter.steer.mock.calls[0]?.[0]).toMatchObject({ text: reply });
    expect(adapter.steer.mock.calls[0]?.[1]).toBe("turn-1");
    expect(store.getCursor("next-reaction")).toBe("👍 to answer");
    // protects=the tap is acknowledged and its buttons give way to the chosen answer, which the history keeps; fails_when=the spinner stays, the buttons stay live, or the answer is not recorded; no_claim=none beyond protects
    expect(answerCallback).toHaveBeenCalledWith("cb-9");
    const done = [
      [{ text: "✓ dogs", callback_data: data.replace(/1$/u, "-") }],
    ];
    expect(edit).toHaveBeenLastCalledWith("1", 100, {
      reply_markup: { inline_keyboard: done },
    });
    const answer = store
      .listTurns(["active"])
      .find((turn) => store.questionReply(turn.id) === reply);
    expect(
      store.messagesForTurn(answer?.id ?? "").map((row) => row.text),
    ).toEqual(["dogs"]);
    await engine.accept([tap(11, data.replace(/1$/u, "0"))], 12);
    await engine.accept([tap(13, data.replace(/1$/u, "-"))], 14);
    // protects=a second tap and the answered button are stale and change nothing; fails_when=the question is answered twice; no_claim=none beyond protects
    expect(adapter.steer).toHaveBeenCalledTimes(1);
    expect(answerCallback).toHaveBeenCalledWith("cb-11", "Already resolved");
    expect(answerCallback).toHaveBeenCalledWith("cb-13", "Already resolved");
    expect(edit).toHaveBeenLastCalledWith("1", 100, {
      reply_markup: { inline_keyboard: done },
    });
    // protects=the answered button already shows the answer, so its tap edits nothing; fails_when=the same markup is re-sent and Telegram rejects it as not modified; no_claim=none beyond protects
    expect(edit).toHaveBeenCalledTimes(2);
    await engine.accept([message(15, "and another thing")], 16);
    // protects=Queue mode still holds a typed message for the next task; fails_when=the answer path steers every follow-up; no_claim=none beyond protects
    expect(adapter.steer).toHaveBeenCalledTimes(1);
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=a tap after the task ended starts the next task with the answer; fails_when=the answer is dropped or still wrapped as a message; why_new=buttons are new; seam=adapter.submit; no_claim=Codex's reaction
it("submits a tapped answer as the next task once nothing runs", async () => {
  const { engine, store, deliverChunk, adapter } = buttonSetup();
  try {
    await engine.accept([message(7, "ask me")], 8);
    await engine.event(asked());
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "turn-1/complete",
      text: "Asked.",
    });
    const data = keyboard(deliverChunk, 0)?.[1]?.[0]?.callback_data ?? "";
    await engine.accept([tap(9, data)], 10);
    // protects=the answer is the next submission's whole text; fails_when=it is not submitted or gets the envelope; no_claim=none beyond protects
    expect(adapter.submit).toHaveBeenCalledTimes(2);
    expect(adapter.submit.mock.calls[1]?.[0]).toMatchObject({ text: reply });
    expect(adapter.steer).not.toHaveBeenCalled();
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=buttons of an earlier session or of another message do not answer; fails_when=a tap after /new reaches the new thread or a forged message ID is accepted; why_new=buttons are new; seam=context.sessionId; no_claim=none
it("treats a tap after /new or from another message as stale", async () => {
  const {
    engine,
    store,
    deliverChunk,
    adapter,
    edit,
    answerCallback,
    context,
  } = buttonSetup();
  try {
    await engine.accept([message(7, "ask me")], 8);
    await engine.event(asked());
    const data = keyboard(deliverChunk, 0)?.[0]?.[0]?.callback_data ?? "";
    await engine.accept([tap(9, data, 101)], 10);
    // protects=a callback that names this question from another message is refused without touching that message; fails_when=the message check is skipped; no_claim=none beyond protects
    expect(answerCallback).toHaveBeenCalledWith("cb-9", "Already resolved");
    expect(edit).not.toHaveBeenCalled();
    (context as { sessionId: string }).sessionId = "session-2";
    await engine.accept([tap(11, data)], 12);
    // protects=after /new the question's buttons are removed and nothing is sent to Codex; fails_when=an old question is answered in the new thread; no_claim=none beyond protects
    expect(answerCallback).toHaveBeenCalledWith("cb-11", "Already resolved");
    expect(edit).toHaveBeenCalledWith("1", 100, {
      reply_markup: { inline_keyboard: [] },
    });
    expect(adapter.steer).not.toHaveBeenCalled();
    expect(adapter.submit).toHaveBeenCalledTimes(1);
    await engine.accept([tap(13, "aq:0123456789abcdef:0")], 14);
    // protects=an unknown question is stale too; fails_when=a missing record throws or is treated as answered; no_claim=none beyond protects
    expect(answerCallback).toHaveBeenCalledWith("cb-13", "Already resolved");
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=items Codex shapes differently still arrive as text; fails_when=a malformed questions field, a long option list or a blank title loses the question; why_new=the button path parses Codex's data; seam=none; no_claim=none
it("keeps questions it cannot offer as buttons readable as text", async () => {
  const { engine, store, deliverChunk } = buttonSetup();
  try {
    await engine.accept([message(7, "ask me")], 8);
    const many = Array.from(
      { length: 11 },
      (_, index) => `option ${String(index)}`,
    );
    await engine.event({
      ...asked({
        delivery: "async",
        itemId: "call_1",
        questions: [
          { title: "Pick one", options: many },
          { title: " ", options: ["yes", "no"] },
          { title: " ", options: null },
        ],
      }),
    });
    await engine.event({
      ...asked({ delivery: "async", questions: "oops" }),
      cursor: "turn-1/item/call_2",
      text: "Free text",
    });
    // protects=the long list and the untitled options are text, the empty question is skipped, and an unreadable item falls back to its text; fails_when=any of them is dropped or sent with buttons; no_claim=none beyond protects
    expect(
      store
        .listOutbox(["sent"])
        .sort((a, b) => b.chunk - a.chunk)
        .map((row) => row.text.replace(/\n+/gu, "\n")),
    ).toEqual([
      ["Pick one", ...many.map((option) => `- ${option}`)].join("\n"),
      "- yes\n- no",
      "Free text",
    ]);
    expect([0, 1, 2].map((call) => keyboard(deliverChunk, call))).toEqual([
      undefined,
      undefined,
      undefined,
    ]);
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=a long option stays readable and answers in full; fails_when=the message hides it behind a cut button or the reply carries the cut label; why_new=button labels are new; seam=adapter.steer; no_claim=how a client wraps a button
it("lists long options in the message and answers with the full option", async () => {
  const { engine, store, deliverChunk, adapter, edit } = buttonSetup();
  try {
    const long = `Use the stored repository prices ${"x".repeat(60)}`;
    await engine.accept([message(7, "ask me")], 8);
    await engine.event(
      asked({
        delivery: "async",
        itemId: "call_1",
        questions: [{ title: "Which prices?", options: [long, "Official"] }],
      }),
    );
    const buttons = keyboard(deliverChunk, 0);
    // protects=the message lists both options and the long button is cut to 64 characters; fails_when=the list is missing or the label is sent whole; no_claim=none beyond protects
    expect(store.listOutbox(["sent"])[0]?.text).toContain(`- ${long}`);
    expect(Array.from(buttons?.[0]?.[0]?.text ?? "")).toHaveLength(64);
    expect(buttons?.[0]?.[0]?.text.endsWith("…")).toBe(true);
    await engine.accept([tap(9, buttons?.[0]?.[0]?.callback_data ?? "")], 10);
    // protects=Codex receives the full option and the title alone as the question; fails_when=the cut label or the listed text is sent; no_claim=none beyond protects
    const sent = adapter.steer.mock.calls[0]?.[0] as { text: string };
    const text = sent.text;
    expect(text).toContain(`"answer":"${long}"`);
    expect(text).toContain('"question":"Which prices?"');
    // protects=the button that shows the long answer is cut like the option buttons; fails_when=the collapsed label carries the whole option; no_claim=Telegram's own label limit
    const shown = edit.mock.calls[0]?.[2] as {
      reply_markup: { inline_keyboard: { text: string }[][] };
    };
    const label = shown.reply_markup.inline_keyboard[0]?.[0]?.text ?? "";
    expect(Array.from(label)).toHaveLength(64);
    expect(label.startsWith("✓ Use the stored")).toBe(true);
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=a tap still answers Codex when Telegram rejects the callback acknowledgement or the button edit, and an out-of-range option index is stale; fails_when=an API failure rejects the tap, drops the answer, or an index past the options answers; why_new=no test makes Telegram fail; seam=none; no_claim=retry of the failed calls (Telegram API mocked)
it("answers even when Telegram rejects the acknowledgement or button edit", async () => {
  const { engine, store, deliverChunk, adapter, edit, answerCallback } =
    buttonSetup();
  try {
    await engine.accept([message(7, "ask me")], 8);
    await engine.event(asked());
    const data = keyboard(deliverChunk, 0)?.[1]?.[0]?.callback_data ?? "";
    await engine.accept([tap(9, data.replace(/1$/u, "7"))], 10);
    // protects=an option index past the list is stale and sends nothing; fails_when=an undefined option answers; no_claim=none beyond protects
    expect(answerCallback).toHaveBeenCalledWith("cb-9", "Already resolved");
    expect(adapter.steer).not.toHaveBeenCalled();
    answerCallback.mockRejectedValue(new Error("query is too old"));
    edit.mockRejectedValue(new Error("message not modified"));
    await engine.accept([tap(11, data)], 12);
    // protects=the answer reaches Codex although both Telegram calls fail; fails_when=the failure aborts the tap before or after the commit; no_claim=none beyond protects
    expect(adapter.steer).toHaveBeenCalledTimes(1);
    expect(adapter.steer.mock.calls[0]?.[0]).toMatchObject({ text: reply });
  } finally {
    await engine.close();
    store.close();
  }
});
