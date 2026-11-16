import { expect, it, vi } from "vitest";
import { message, setup } from "./continuation-fixture.js";

async function linkedRecovery(engine: ReturnType<typeof setup>["engine"]) {
  await engine.accept([message(7, "review my PR")], 8);
  await engine.event({
    kind: "turn-completed",
    sessionId: "thread",
    turnId: "turn-1",
    cursor: "turn-1/complete",
    data: { status: "interrupted" },
  });
  await engine.event({
    kind: "turn-started",
    sessionId: "thread",
    turnId: "recovery",
    cursor: "recovery/start",
    data: { rootTurnId: "turn-1" },
  });
}

it("asks the original user, not the latest chat, to approve a continuation's command", async () => {
  const { engine, store, context } = setup();
  (context.adapter.capabilities as Set<string>).add("approve");
  context.adapter.approve = vi.fn().mockResolvedValue(undefined);
  try {
    await linkedRecovery(engine);
    store.setCursor("chat-id", "2");
    store.setCursor("user-id", "2");
    await engine.event({
      kind: "approval-requested",
      sessionId: "thread",
      turnId: "recovery",
      requestId: "approve-1",
      cursor: "approval/approve-1",
      data: { command: "git push" },
    });
    const [request] = store.listRequests(["pending"]);
    const payload = JSON.parse(request?.payload ?? "{}") as {
      requestId?: string;
      chatId?: string;
      userId?: string;
    };
    // protects=approval ownership of a continuation stays with the user whose task it continues; fails_when=the latest chat's user receives and can approve the command; no_claim=Telegram callback delivery (channel is a fake)
    expect(payload).toEqual(
      expect.objectContaining({
        requestId: "approve-1",
        chatId: "1",
        userId: "1",
      }),
    );
  } finally {
    await engine.close();
    store.close();
  }
});

it("does not mirror a continuation's input as terminal typing", async () => {
  const { engine, store, deliverChunk } = setup({ mirror_non_tg_input: true });
  try {
    await linkedRecovery(engine);
    const before = deliverChunk.mock.calls.length;
    await engine.event({
      kind: "input",
      sessionId: "thread",
      turnId: "recovery",
      cursor: "recovery/input",
      text: "Continue the interrupted review",
    });
    // protects=Codex's resume input for a Telegram task is not shown as terminal input in the latest chat; fails_when=the recovery prompt is mirrored with the terminal marker; no_claim=mirroring of genuine terminal turns (covered elsewhere)
    expect(deliverChunk.mock.calls.length).toBe(before);
  } finally {
    await engine.close();
    store.close();
  }
});

it("answers a continuation in the format of the turn it continues", async () => {
  const { engine, store } = setup();
  try {
    store.setCursor("message-format/2", "regular");
    await engine.accept([message(7, "review my PR")], 8);
    const original = store.listTurns(["active", "submitting"])[0];
    store.setCursor(`turn-format/${original?.id ?? ""}`, "rich");
    store.setCursor("chat-id", "2");
    await linkedRecovery(engine).catch(() => undefined);
    const recovery = store
      .listTurns(["active"])
      .find((turn) => turn.externalTurnId === "recovery");
    // protects=a continuation keeps the original turn's answer format even though it was created while another chat was latest; fails_when=the format cached from the latest chat sticks; no_claim=rendering of either format
    expect(store.getCursor(`turn-format/${recovery?.id ?? ""}`)).toBe("rich");
  } finally {
    await engine.close();
    store.close();
  }
});
