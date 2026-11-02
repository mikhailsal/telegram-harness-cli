import { expect, it, vi } from "vitest";
import type { ChannelEvent } from "../../src/channels/index.js";
import { Commands } from "../../src/core/commands.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { catalog } from "../../src/channels/index.js";

const command: Extract<ChannelEvent, { kind: "command" }> = {
  kind: "command",
  command: "new",
  arguments: "",
  updateId: 1,
  chatId: "1",
  userId: "1",
  messageId: 1,
  sentAt: "2026-11-02T00:00:00Z",
  language: "en",
};
function setup(ready: boolean, active: boolean) {
  const rotate = vi.fn().mockResolvedValue(undefined);
  const send = vi.fn().mockResolvedValue(1);
  const setCursor = vi.fn();
  const context = {
    store: { listTurns: () => (active ? [{}] : []), setCursor },
    adapterReady: ready,
    rotateSession: rotate,
    transient: { send },
    now: () => new Date("2026-11-02T00:00:00Z"),
  } as unknown as RuntimeContext;
  const service = new Commands(context, { finish: () => false });
  return { service, rotate, send, setCursor };
}
it("confirms a new idle session after it has started", async () => {
  const f = setup(true, false);
  await f.service.run(command);
  expect(f.rotate).toHaveBeenCalledWith(command.updateId);
  expect(f.send).toHaveBeenCalledWith(
    expect.objectContaining({ kind: "command" }),
    catalog.en.newStarted,
  );
});
it.each([
  [true, true],
  [false, false],
])(
  "defers /new silently when active=%s or disconnected=%s",
  async (ready, active) => {
    const f = setup(ready, active);
    await f.service.run(command);
    expect(f.setCursor).toHaveBeenCalledWith("deferred-new", "1");
    expect(f.setCursor).toHaveBeenCalledWith(
      "deferred-new-command",
      expect.stringContaining('"updateId":1'),
    );
    expect(f.rotate).not.toHaveBeenCalled();
    expect(f.send).not.toHaveBeenCalled();
  },
);
