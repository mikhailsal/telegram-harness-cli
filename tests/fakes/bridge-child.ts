import type {
  AdapterCapability,
  AdapterEvent,
  AdapterInput,
  AdapterSession,
  ApprovalDecision,
  HarnessAdapter,
} from "../../src/adapters/contract.js";
const { startBridge } = (await import(
  new URL("../../dist/core/runtime.js", import.meta.url).href
)) as typeof import("../../src/core/runtime.js");
const { parseConfig } = (await import(
  new URL("../../dist/core/config.js", import.meta.url).href
)) as typeof import("../../src/core/config.js");
const { Store } = (await import(
  new URL("../../dist/store/index.js", import.meta.url).href
)) as typeof import("../../src/store/index.js");

const root = process.env.THC_TEST_ROOT;
const server = process.env.THC_TEST_SERVER;
if (!root || !server) throw new Error("Missing test fixture configuration");
const apiRoot = server;
process.env.THC_SYNTHETIC_TOKEN = `${process.env.THC_TEST_BOT_ID ?? "123"}:synthetic`;
if (process.env.THC_TEST_HOLD_INBOX === "1") {
  const inherited = Object.getPrototypeOf(
    Store.prototype,
  ) as typeof Store.prototype;
  Store.prototype.recordIncoming = function (
    ...args: Parameters<typeof inherited.recordIncoming>
  ) {
    const result = inherited.recordIncoming.call(this, ...args);
    if (result) {
      process.send?.({ boundary: "inbox-committed" });
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 30000);
    }
    return result;
  };
}
async function call<T>(path: string, data: unknown): Promise<T> {
  const response = await fetch(`${apiRoot}/harness/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok)
    throw new Error(`Fake harness returned ${String(response.status)}`);
  return response.json() as Promise<T>;
}
class RemoteAdapter implements HarnessAdapter {
  readonly capabilities: ReadonlySet<AdapterCapability> = new Set([
    "submit",
    "observe",
    "interrupt",
    "approve",
    "reconcile",
    "newSession",
    "attach",
  ]);
  closed = false;
  cursor = 0;
  newSession(): Promise<AdapterSession> {
    return Promise.resolve({ id: "fake-session", cursor: "0" });
  }
  submit(input: AdapterInput): Promise<{ turnId: string }> {
    return call("submit", input);
  }
  async *observe(): AsyncIterable<AdapterEvent> {
    while (!this.closed) {
      const result = await call<{ events: AdapterEvent[] }>("events", {
        cursor: this.cursor,
      });
      for (const event of result.events) {
        this.cursor = Number(event.cursor);
        yield event;
      }
      await new Promise((resolve) => setTimeout(resolve, 30));
    }
  }
  async reconcile(): Promise<AdapterEvent[]> {
    const result = await call<{ events: AdapterEvent[] }>("events", {
      cursor: 0,
    });
    this.cursor = result.events.length;
    return result.events;
  }
  interrupt(): Promise<void> {
    return Promise.resolve();
  }
  async approve(decision: ApprovalDecision): Promise<void> {
    await call("approve", decision);
  }
  attach(): Promise<{ command: string; args: string[] }> {
    return Promise.resolve({ command: "true", args: [] });
  }
  close(): Promise<void> {
    this.closed = true;
    return Promise.resolve();
  }
  pendingState(): Promise<"none"> {
    return Promise.resolve("none");
  }
}
const config = parseConfig({
  token: { env: "THC_SYNTHETIC_TOKEN" },
  allowlist: [1],
  sandbox: "read-only",
  status: process.env.THC_TEST_STATUS === "message" ? "message" : "off",
  update_check: false,
  codex: { adapter: "protocol", daemon_autostart: false },
  submit_ack_timeout: 20,
});
const fixedTime = process.env.THC_TEST_NOW;
await startBridge(root, config, {
  apiRoot: server,
  adapterFactory: () => Promise.resolve(new RemoteAdapter()),
  tickIntervalMs: 20,
  ...(fixedTime ? { now: () => new Date(fixedTime) } : {}),
});
