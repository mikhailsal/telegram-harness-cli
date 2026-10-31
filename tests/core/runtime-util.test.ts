import { expect, it } from "vitest";
import {
  delay,
  offlineAdapter,
  untilAbort,
} from "../../src/core/runtime-util.js";

it("never submits or approves while the adapter is disconnected", async () => {
  const adapter = offlineAdapter();
  expect(adapter.capabilities.size).toBe(0);
  await expect(
    adapter.submit({ correlationId: "x", text: "task" }),
  ).rejects.toThrow(/disconnected/);
  await expect(
    adapter.approve({ requestId: "r", decision: "accept" }),
  ).rejects.toThrow(/disconnected/);
  await expect(adapter.reconcile()).rejects.toThrow(/disconnected/);
  await expect(adapter.interrupt("t")).rejects.toThrow(/disconnected/);
  await expect(adapter.attach()).rejects.toThrow(/disconnected/);
  await expect(adapter.newSession()).rejects.toThrow(/disconnected/);
  await expect(
    adapter.observe()[Symbol.asyncIterator]().next(),
  ).resolves.toMatchObject({ done: true });
  await adapter.close();
});
it("cancels waits and async work promptly without leaking a rejection", async () => {
  const controller = new AbortController();
  const waiting = delay(60000, controller.signal);
  const task = untilAbort(
    new Promise<string>(() => undefined),
    controller.signal,
  );
  controller.abort();
  await expect(waiting).resolves.toBeUndefined();
  await expect(task).resolves.toBeUndefined();
  await expect(delay(60000, controller.signal)).resolves.toBeUndefined();
  await expect(
    untilAbort(Promise.resolve("value"), controller.signal),
  ).resolves.toBeUndefined();
});
it("preserves successful results and propagates operation errors", async () => {
  const controller = new AbortController();
  await expect(
    untilAbort(Promise.resolve("ok"), controller.signal),
  ).resolves.toBe("ok");
  await expect(
    untilAbort(
      Promise.reject(new Error("operation failed")),
      controller.signal,
    ),
  ).rejects.toThrow("operation failed");
});
