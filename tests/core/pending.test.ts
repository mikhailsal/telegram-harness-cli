import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { openStore, type Store } from "../../src/store/index.ts";
import type { RuntimeContext } from "../../src/core/context.ts";
import type { AdapterEvent } from "../../src/adapters/contract.ts";
import type { ChannelEvent } from "../../src/channels/types.ts";
import { PendingManager } from "../../src/core/pending.ts";
import { queueRequestSummaries } from "../../src/core/request-summary.ts";

function makeTransient(store: Store, now: Date) {
  const remove = vi.fn((id: string) => {
    store.deleteTransient(id);
    return Promise.resolve();
  });
  const transient = {
    create: vi.fn((intent: Parameters<typeof store.putTransient>[0]) => {
      store.putTransient(intent);
      store.setTransientMessage(intent.id, 10, now.toISOString());
      return Promise.resolve(10);
    }),
    remove,
    repost: vi.fn().mockResolvedValue(false),
  };
  return { transient, remove };
}
const event: AdapterEvent = {
  kind: "approval-requested",
  sessionId: "session",
  cursor: "approval/7",
  turnId: "external-turn",
  requestId: "7",
  data: { command: "echo safe" },
};
async function setup(timeout: number | null = null) {
  const root = await mkdtemp(join(tmpdir(), "thc-pending-"));
  const store = openStore(join(root, "db.sqlite"));
  const now = new Date("2026-10-31T00:00:00Z");
  store.putEntity({
    id: "e",
    directory: root,
    botId: "bot",
    createdAt: now.toISOString(),
  });
  store.createTurn({
    id: "turn",
    entityId: "e",
    source: "telegram",
    createdAt: now.toISOString(),
  });
  store.transitionTurn("turn", "queued", "submitting");
  store.transitionTurn("turn", "submitting", "active", {
    externalTurnId: "external-turn",
  });
  const approve = vi.fn().mockResolvedValue(undefined);
  const interrupt = vi.fn().mockResolvedValue(undefined);
  const answerCallback = vi.fn().mockResolvedValue(undefined);
  const { transient, remove } = makeTransient(store, now);
  const context = {
    store,
    adapter: { capabilities: new Set(["approve"]), approve, interrupt },
    channel: { answerCallback },
    transient,
    config: { approval_timeout: timeout },
    now: () => now,
    log: vi.fn(),
  } as unknown as RuntimeContext;
  const manager = new PendingManager(context);
  return {
    root,
    store,
    context,
    manager,
    event,
    approve,
    interrupt,
    answerCallback,
    remove,
  };
}
function callback(
  id: string,
  overrides: Partial<Extract<ChannelEvent, { kind: "decision" }>> = {},
): Extract<ChannelEvent, { kind: "decision" }> {
  return {
    kind: "decision",
    updateId: 4,
    chatId: "chat",
    userId: "user",
    messageId: 10,
    sentAt: "2026-10-31T00:00:00Z",
    language: "en",
    callbackId: "callback",
    data: `p:${id}:a`,
    ...overrides,
  };
}
it("persists approval and checks user, chat, message and turn before CAS", async () => {
  const f = await setup();
  try {
    await f.manager.request(f.event, "turn", "chat", "user", "en");
    await f.manager.request(f.event, "turn", "chat", "user", "en");
    const row = f.store.listRequests(["pending"])[0];
    if (!row) throw new Error("missing request");
    await f.manager.decision(callback(row.id, { userId: "other" }));
    await f.manager.decision(callback(row.id, { messageId: 11 }));
    expect(f.approve).not.toHaveBeenCalled();
    f.approve.mockImplementationOnce(() => {
      f.context.now().setTime(f.context.now().getTime() + 120000);
      return Promise.resolve();
    });
    await f.manager.decision(callback(row.id));
    expect(f.approve).toHaveBeenCalledExactlyOnceWith({
      requestId: "7",
      decision: "accept",
    });
    expect(f.store.getRequest(row.id)?.status).toBe("answered");
    f.context.config.timezone = "UTC";
    queueRequestSummaries(f.context);
    queueRequestSummaries(f.context);
    const entries = f.store.searchMessages("echo safe", 10).messages;
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      author: "system",
      text: "You allowed: echo safe · 00:00",
    });
    expect(
      f.store.listOutbox(["pending", "sending", "sent", "failed"]),
    ).toHaveLength(0);
    await f.manager.decision(callback(row.id));
    expect(f.approve).toHaveBeenCalledTimes(1);
  } finally {
    f.store.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
it("never repeats an uncertain response; missing replay becomes stale and interrupts", async () => {
  const f = await setup();
  try {
    f.approve.mockRejectedValueOnce(new Error("disconnected"));
    await f.manager.request(f.event, "turn", "chat", "user", "en");
    const row = f.store.listRequests(["pending"])[0];
    if (!row) throw new Error("missing request");
    await f.manager.decision(callback(row.id));
    expect(f.store.getRequest(row.id)?.status).toBe("answering");
    await f.manager.recover(new Set(["7"]));
    expect(f.approve).toHaveBeenCalledTimes(1);
    await f.manager.recover(new Set());
    expect(f.store.getRequest(row.id)?.status).toBe("stale");
    expect(f.interrupt).toHaveBeenCalledExactlyOnceWith("external-turn");
    await f.manager.request(f.event, "turn", "chat", "user", "en");
    expect(f.store.listRequests(["pending"])).toHaveLength(0);
    expect(f.approve).toHaveBeenCalledTimes(1);
  } finally {
    f.store.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
it("resolves requests from another client and cancels on turn stop", async () => {
  const f = await setup();
  try {
    await f.manager.request(f.event, "turn", "chat", "user", "en");
    const row = f.store.listRequests(["pending"])[0];
    if (!row) throw new Error("missing request");
    await f.manager.resolved({ ...f.event, kind: "approval-resolved" });
    expect(f.store.getRequest(row.id)?.status).toBe("resolved_elsewhere");
    await f.manager.request(
      { ...f.event, requestId: "8" },
      "turn",
      "chat",
      "user",
      "en",
    );
    await f.manager.cancelTurn("turn");
    expect(f.store.listRequests(["cancelled"])).toHaveLength(1);
  } finally {
    f.store.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
function recordQuestionInbox(store: Store, updateId: number): void {
  store.recordInbox({
    updateId,
    entityId: "e",
    chatId: "chat",
    userId: "user",
    receivedAt: "2026-10-31T00:00:00Z",
    payload: "{}",
  });
}
function questionFixture(base: AdapterEvent): AdapterEvent {
  return {
    ...base,
    kind: "question-requested",
    requestId: "9",
    data: {
      questions: [
        {
          id: "first",
          question: "Choose",
          options: [{ label: "Blue", description: "Color" }],
        },
        { id: "second", question: "Explain", options: null },
      ],
    },
  };
}
it("collects several question answers and sends the structured response once", async () => {
  const f = await setup();
  try {
    const event = questionFixture(f.event);
    await f.manager.request(event, "turn", "chat", "user", "en");
    const row = f.store.listRequests(["pending"])[0];
    if (!row) throw new Error("missing request");
    expect(row.kind).toBe("question");
    recordQuestionInbox(f.store, 4);
    await f.manager.decision(callback(row.id, { data: `p:${row.id}:q0-0` }));
    expect(f.approve).not.toHaveBeenCalled();
    const text: Extract<ChannelEvent, { kind: "message" }> = {
      kind: "message",
      updateId: 5,
      chatId: "chat",
      userId: "user",
      messageId: 11,
      sentAt: "2026-10-31T00:00:00Z",
      language: "en",
      text: "because",
      attachments: [],
    };
    recordQuestionInbox(f.store, 5);
    expect(await f.manager.textAnswer(text)).toBe(true);
    expect(f.approve).toHaveBeenCalledExactlyOnceWith({
      requestId: "9",
      decision: {
        answers: {
          first: { answers: ["Blue"] },
          second: { answers: ["because"] },
        },
      },
    });
    expect(f.store.getRequest(row.id)?.status).toBe("answered");
  } finally {
    f.store.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
it("refuses secret questions rather than leaking responses into chat history", async () => {
  const f = await setup();
  try {
    await expect(
      f.manager.request(
        {
          ...f.event,
          kind: "question-requested",
          data: {
            questions: [
              {
                id: "secret",
                question: "password",
                isSecret: true,
                options: null,
              },
            ],
          },
        },
        "turn",
        "chat",
        "user",
        "en",
      ),
    ).rejects.toThrow("Secret questions");
    expect(f.store.listRequests(["pending"])).toHaveLength(0);
  } finally {
    f.store.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
it("declines an expired approval only when timeout is configured", async () => {
  const f = await setup(30);
  try {
    await f.manager.request(f.event, "turn", "chat", "user", "en");
    const row = f.store.listRequests(["pending"])[0];
    if (!row) throw new Error("missing request");
    f.context.now = () => new Date("2026-10-31T00:00:31Z");
    await f.manager.tick();
    expect(f.approve).toHaveBeenCalledExactlyOnceWith({
      requestId: "7",
      decision: "decline",
    });
    expect(f.store.getRequest(row.id)?.status).toBe("expired");
  } finally {
    f.store.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
it("retries stale-turn interruption after a transport failure", async () => {
  const f = await setup();
  try {
    await f.manager.request(f.event, "turn", "chat", "user", "en");
    const row = f.store.listRequests(["pending"])[0];
    if (!row) throw new Error("missing request");
    f.interrupt.mockRejectedValueOnce(new Error("offline"));
    await f.manager.recover(new Set());
    expect(f.store.getRequest(row.id)?.status).toBe("stale");
    expect(f.store.getCursor(`request-interrupt/${row.id}`)).toBe(
      "external-turn",
    );
    await f.manager.tick();
    expect(f.interrupt).toHaveBeenCalledTimes(2);
    expect(f.store.getCursor(`request-interrupt/${row.id}`)).toBe("");
  } finally {
    f.store.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
it("renders a silent replacement question card with bound answer buttons", async () => {
  const { sendQuestionCard } =
    await import("../../src/core/pending-question.ts");
  const sendMessage = vi.fn().mockResolvedValue({ message_id: 22 });
  const context = {
    channel: { bot: { api: { sendMessage } } },
  } as unknown as RuntimeContext;
  const question = {
    id: "q",
    question: "Pick one",
    isSecret: false,
    options: [{ label: "Yes", description: "Affirmative" }],
  };
  expect(
    await sendQuestionCard(context, "chat", "request", 0, question, true),
  ).toBe(22);
  expect(sendMessage).toHaveBeenCalledWith(
    "chat",
    "Pick one",
    expect.objectContaining({
      disable_notification: true,
      reply_markup: {
        inline_keyboard: [[{ text: "Yes", callback_data: "p:request:q0-0" }]],
      },
    }),
  );
});
it("does not apply a committed partial text answer twice after a crash", async () => {
  const f = await setup();
  try {
    const event: AdapterEvent = {
      ...f.event,
      kind: "question-requested",
      requestId: "two",
      data: {
        questions: [
          { id: "first", question: "First", options: null },
          { id: "second", question: "Second", options: null },
        ],
      },
    };
    await f.manager.request(event, "turn", "chat", "user", "en");
    const row = f.store.listRequests(["pending"])[0];
    if (!row) throw new Error("missing request");
    const text: Extract<ChannelEvent, { kind: "message" }> = {
      kind: "message",
      updateId: 44,
      chatId: "chat",
      userId: "user",
      messageId: 11,
      sentAt: "2026-10-31T00:00:00Z",
      language: "en",
      text: "first answer",
      attachments: [],
    };
    f.store.recordInbox({
      updateId: 44,
      entityId: "e",
      chatId: "chat",
      userId: "user",
      receivedAt: text.sentAt,
      payload: "{}",
    });
    f.remove.mockRejectedValueOnce(new Error("simulated crash"));
    await expect(f.manager.textAnswer(text)).rejects.toThrow("simulated crash");
    expect(f.store.getInbox(44)?.status).toBe("consumed");
    const restarted = new PendingManager(f.context);
    expect(await restarted.textAnswer(text)).toBe(false);
    await restarted.tick();
    expect(JSON.parse(f.store.getCursor(`question/${row.id}`) ?? "{}")).toEqual(
      { first: { answers: ["first answer"] } },
    );
    expect(f.approve).not.toHaveBeenCalled();
  } finally {
    f.store.close();
    await rm(f.root, { recursive: true, force: true });
  }
});
