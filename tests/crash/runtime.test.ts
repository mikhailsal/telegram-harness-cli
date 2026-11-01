import { existsSync, readdirSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, it } from "vitest";
import { openStore } from "../../src/store/index.js";
import { fixture, waitUntil } from "../fakes/runtime-fixture.js";

it("recovers a committed inbox after SIGKILL before offset acknowledgement", async () => {
  const f = await fixture();
  f.server.input(1, "first task");
  const child = f.start(true);
  let boundary = false;
  child.on("message", (message: unknown) => {
    if (
      typeof message === "object" &&
      message !== null &&
      "boundary" in message
    )
      boundary = true;
  });
  await waitUntil(() => boundary);
  await f.kill();
  const store = openStore(join(f.root, ".thc", "store.db"));
  expect(store.getInbox(1)).toBeDefined();
  expect(store.getCursor("telegram-offset")).toBeUndefined();
  store.close();
  f.start();
  await waitUntil(() => f.server.submissions.length === 1);
  expect(f.server.submissions[0]?.text).toContain("first task");
  await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  expect(f.server.submissions).toHaveLength(1);
}, 30000);
it("reconciles an accepted submit after SIGKILL without running the task twice", async () => {
  const f = await fixture();
  f.server.holdSubmit = true;
  f.server.input(1, "slow task");
  f.start();
  await waitUntil(() => f.server.submissions.length === 1);
  const correlationId = f.server.submissions[0]?.correlationId;
  if (!correlationId) throw new Error("Missing submission correlation");
  f.server.event("turn-started", "turn-1", { correlationId });
  f.server.event("turn-completed", "turn-1", {
    correlationId,
    text: "recovered answer",
    data: { status: "completed" },
  });
  await f.kill();
  f.server.holdSubmit = false;
  f.start();
  await waitUntil(() =>
    f.server.delivered.some((entry) =>
      JSON.stringify(entry.body).includes("recovered answer"),
    ),
  );
  expect(f.server.submissions).toHaveLength(1);
}, 30000);
it("resends an accepted but unrecorded answer with the duplication flag after SIGKILL", async () => {
  const f = await fixture();
  f.server.input(1, "task");
  f.server.holdDelivery = true;
  f.start();
  await waitUntil(() => f.server.submissions.length === 1);
  const correlationId = f.server.submissions[0]?.correlationId;
  if (!correlationId) throw new Error("Missing correlation");
  f.server.event("turn-started", "turn-1", { correlationId });
  f.server.event("turn-completed", "turn-1", {
    correlationId,
    text: "final answer",
    data: { status: "completed" },
  });
  await waitUntil(() =>
    f.server.delivered.some((entry) => entry.method === "sendRichMessage"),
  );
  await f.kill();
  f.server.holdDelivery = false;
  f.start();
  await waitUntil(
    () =>
      f.server.delivered.filter((entry) => entry.method === "sendRichMessage")
        .length >= 2,
    20000,
  );
  await waitUntil(() => {
    const store = openStore(join(f.root, ".thc", "store.db"));
    try {
      return store
        .messagesForTurn(correlationId)
        .some(
          (message) =>
            message.author === "assistant" && message.possiblyDuplicated,
        );
    } finally {
      store.close();
    }
  });
  expect(f.server.submissions).toHaveLength(1);
  expect(
    f.server.delivered.filter((entry) => entry.method === "sendRichMessage"),
  ).toHaveLength(2);
}, 30000);

it("queues a follow-up until the active turn completes and delivers both answers", async () => {
  const f = await fixture();
  f.server.input(1, "first task");
  f.start();
  await waitUntil(() => f.server.submissions.length === 1);
  const first = f.server.submissions[0]?.correlationId;
  if (!first) throw new Error("Missing first correlation");
  f.server.input(2, "follow-up");
  await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  expect(f.server.submissions).toHaveLength(1);
  f.server.event("turn-completed", "turn-1", {
    correlationId: first,
    text: "first answer",
    data: { status: "completed" },
  });
  await waitUntil(() => f.server.submissions.length === 2);
  const second = f.server.submissions[1]?.correlationId;
  if (!second) throw new Error("Missing second correlation");
  f.server.event("turn-completed", "turn-2", {
    correlationId: second,
    text: "second answer",
    data: { status: "completed" },
  });
  await waitUntil(
    () =>
      f.server.delivered.filter((entry) => entry.method === "sendRichMessage")
        .length === 2,
  );
  expect(f.server.submissions[1]?.text).toContain("follow-up");
}, 30000);

it("applies an approval only once when duplicate callback updates arrive", async () => {
  const f = await fixture();
  f.server.input(1, "task requiring approval");
  f.start();
  await waitUntil(() => f.server.submissions.length === 1);
  const correlationId = f.server.submissions[0]?.correlationId;
  if (!correlationId) throw new Error("Missing correlation");
  f.server.event("approval-requested", "turn-1", {
    correlationId,
    requestId: "request-1",
    data: { command: "echo hello" },
  });
  await waitUntil(() =>
    f.server.delivered.some((entry) =>
      JSON.stringify(entry.body).includes("p:"),
    ),
  );
  const card = f.server.delivered.find((entry) =>
    JSON.stringify(entry.body).includes("p:"),
  );
  const markup = card?.body.reply_markup as
    { inline_keyboard: { callback_data: string }[][] } | undefined;
  const data = markup?.inline_keyboard[0]?.[0]?.callback_data;
  if (!data) throw new Error("Missing approval button");
  f.server.callback(2, data);
  f.server.callback(3, data);
  await waitUntil(() => f.server.decisions.length === 1);
  await new Promise((resolveWait) => setTimeout(resolveWait, 200));
  expect(f.server.decisions).toEqual([
    { requestId: "request-1", decision: "accept" },
  ]);
}, 30000);

it("resumes a partially streamed attachment after SIGKILL without duplicate files", async () => {
  const f = await fixture();
  f.server.holdDownload = true;
  f.server.attachment(1);
  f.start();
  const folder = join(f.root, ".thc", "files", "2025-10-09");
  await waitUntil(
    () =>
      f.server.downloads === 1 &&
      existsSync(folder) &&
      readdirSync(folder).some((name) => name.endsWith(".part")),
  );
  await f.kill();
  f.server.holdDownload = false;
  f.start();
  await waitUntil(() => f.server.submissions.length === 1);
  const names = readdirSync(folder);
  expect(names).toHaveLength(1);
  expect(names[0]).not.toMatch(/\.part$/);
  const path = join(folder, names[0] ?? "");
  expect(await readFile(path, "utf8")).toBe("complete attachment");
  expect(f.server.downloads).toBe(2);
  expect(f.server.submissions[0]?.text).toContain("attachment caption");
  expect(f.server.submissions[0]?.text).toContain(path);
  const store = openStore(join(f.root, ".thc", "store.db"));
  try {
    expect(store.listAttachments()).toHaveLength(1);
  } finally {
    store.close();
  }
}, 30000);

function transients(root: string) {
  const store = openStore(join(root, ".thc", "store.db"));
  try {
    return store.listTransients();
  } finally {
    store.close();
  }
}
function deleted(
  calls: { method: string; body: Record<string, unknown> }[],
  ids: (number | null | undefined)[],
): boolean {
  return ids.every((id) =>
    calls.some(
      (call) => call.method === "deleteMessage" && call.body.message_id === id,
    ),
  );
}
function hasTransient(root: string, kind: string): boolean {
  return transients(root).some(
    (row) => row.kind === kind && row.messageId !== null,
  );
}
async function expireCommands(f: Awaited<ReturnType<typeof fixture>>) {
  const initial = openStore(join(f.root, ".thc", "store.db"));
  initial.setCursor("welcome-pending", "initial");
  initial.close();
  f.server.input(1, "/status");
  f.start(false, "2026-01-01T00:00:00.000Z", "message");
  await waitUntil(
    () =>
      transients(f.root).filter(
        (row) => row.kind === "command" && row.messageId !== null,
      ).length === 1 && hasTransient(f.root, "welcome"),
  );
  const original = transients(f.root);
  const commands = original
    .filter((row) => row.kind === "command")
    .map((row) => row.messageId);
  const welcome = original.find((row) => row.kind === "welcome")?.messageId;
  await f.kill();
  f.start(false, "2026-01-01T00:11:00.000Z", "message");
  await waitUntil(
    () =>
      deleted(f.server.calls, commands) &&
      !transients(f.root).some((row) => row.kind === "command"),
  );
  expect(transients(f.root).map((row) => row.kind)).toEqual(["welcome"]);
  expect(deleted(f.server.calls, [1])).toBe(false);
  return welcome;
}
it("preserves the command and expires bot notices while renewing active status across restarts", async () => {
  const f = await fixture();
  const welcome = await expireCommands(f);
  f.server.input(2, "long running task");
  await waitUntil(
    () => f.server.submissions.length === 1 && hasTransient(f.root, "status"),
  );
  const status = transients(f.root).find(
    (row) => row.kind === "status",
  )?.messageId;
  await f.kill();
  f.start(false, "2026-01-02T23:12:00.000Z", "message");
  await waitUntil(
    () =>
      deleted(f.server.calls, [welcome, status]) &&
      !hasTransient(f.root, "welcome") &&
      transients(f.root).some(
        (row) => row.kind === "status" && row.messageId !== status,
      ),
  );
  const renewed = transients(f.root).find((row) => row.kind === "status");
  const correlationId = f.server.submissions[0]?.correlationId;
  if (!correlationId) throw new Error("Missing active turn");
  f.server.event("turn-completed", "turn-1", {
    correlationId,
    text: "finished long task",
  });
  await waitUntil(
    () =>
      deleted(f.server.calls, [renewed?.messageId]) &&
      !hasTransient(f.root, "status"),
  );
  expect(deleted(f.server.calls, [1])).toBe(false);
}, 30000);

it("silently drops group updates and reveals only a stranger's ID over the real local Bot API", async () => {
  const f = await fixture();
  f.server.updates.push({
    update_id: 1,
    message: {
      message_id: 1,
      date: 1760000000,
      chat: { id: -9, type: "group", title: "Group" },
      from: { id: 1, is_bot: false, first_name: "Tester" },
      text: "group task",
    },
  });
  f.server.updates.push({
    update_id: 2,
    message: {
      message_id: 2,
      date: 1760000000,
      chat: { id: 2, type: "private" },
      from: { id: 2, is_bot: false, first_name: "Stranger" },
      text: "stranger task",
    },
  });
  f.server.input(3, "authorized task");
  f.start();
  await waitUntil(() => f.server.submissions.length === 1);
  const messages = f.server.delivered.filter(
    (entry) => entry.method === "sendMessage",
  );
  expect(messages).toHaveLength(1);
  expect(messages[0]?.body).toMatchObject({ chat_id: 2, text: "2" });
  expect(f.server.submissions[0]?.text).toContain("authorized task");
  const store = openStore(join(f.root, ".thc", "store.db"));
  try {
    expect(store.getInbox(1)).toBeUndefined();
    expect(store.getInbox(2)).toBeUndefined();
    expect(store.getInbox(3)).toBeDefined();
    expect(store.getCursor("telegram-offset")).toBe("4");
  } finally {
    store.close();
  }
}, 30000);
