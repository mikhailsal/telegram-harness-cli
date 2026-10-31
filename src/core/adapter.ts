import { join } from "node:path";
import { z } from "zod";
import type { HarnessAdapter } from "../adapters/contract.js";
import { ProtocolAdapter } from "../adapters/codex-protocol/index.js";
import { CodexTmuxAdapter } from "../adapters/codex-tmux/index.js";
import type { Store } from "../store/index.js";
import type { Config } from "./config.js";
import { codexHome } from "./config.js";
import { nativeCodex, sandboxPolicy } from "./codex.js";

const pendingSchema = z.strictObject({
  correlationId: z.string(),
  envelope: z.string(),
});
const descriptorSchema = z.strictObject({
  server: z.string(),
  localId: z.string(),
  identity: z.strictObject({
    paneId: z.string(),
    pid: z.string(),
    executable: z.string(),
    startTime: z.string(),
  }),
  cwd: z.string(),
  threadId: z.string(),
  rolloutPath: z.string(),
  cursor: z.number().int().nonnegative(),
  startedAt: z.number(),
  originator: z.string(),
});
export const managedDescriptorSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("protocol"), sessionId: z.string() }),
  z.strictObject({ kind: z.literal("tmux"), descriptor: descriptorSchema }),
]);

function pendingSubmissions(store: Store) {
  return store
    .listTurns(["queued", "submitting", "active", "uncertain"])
    .flatMap((turn) => {
      const raw = store.getCursor(`tmux-submission/${turn.id}`);
      return raw ? [pendingSchema.parse(JSON.parse(raw) as unknown)] : [];
    });
}

function protocolAdapter(
  directory: string,
  config: Config,
  store: Store,
  sessionId?: string,
): ProtocolAdapter {
  return new ProtocolAdapter({
    socketPath:
      config.codex.socket ??
      join(codexHome(config), "app-server-control", "app-server-control.sock"),
    cwd: directory,
    sandbox: config.sandbox,
    approvalPolicy: "on-request",
    codexPath: config.codex.executable,
    ...(sessionId ? { sessionId } : {}),
    uncertainCorrelationIds: store
      .listTurns(["submitting", "uncertain", "active"])
      .map((turn) => turn.id),
    uncertainApprovalIds: store
      .listRequests(["answering"])
      .map(
        (request) =>
          z
            .object({ requestId: z.string() })
            .parse(JSON.parse(request.payload) as unknown).requestId,
      ),
  });
}
async function tmuxOptions(directory: string, config: Config, store: Store) {
  return {
    cwd: directory,
    codexHome: codexHome(config),
    sandbox: config.sandbox,
    executable: await nativeCodex(config.codex.executable, directory),
    ackTimeoutMs: config.submit_ack_timeout * 1000,
    persistSubmission: (submission: z.infer<typeof pendingSchema>) => {
      store.setCursor(
        `tmux-submission/${submission.correlationId}`,
        JSON.stringify(submission),
      );
      return Promise.resolve();
    },
  };
}
export async function openAdapter(
  directory: string,
  config: Config,
  store: Store,
): Promise<HarnessAdapter> {
  const stored = store.getCursor("adapter-descriptor");
  const descriptor = stored
    ? managedDescriptorSchema.parse(JSON.parse(stored) as unknown)
    : undefined;
  if (descriptor && descriptor.kind !== config.codex.adapter)
    throw new Error(
      "Configured adapter differs from the saved session; finish the session before switching",
    );
  if (config.codex.adapter === "protocol")
    return protocolAdapter(
      directory,
      config,
      store,
      descriptor?.kind === "protocol" ? descriptor.sessionId : undefined,
    );
  const options = await tmuxOptions(directory, config, store);
  return descriptor?.kind === "tmux"
    ? CodexTmuxAdapter.restore(
        options,
        descriptor.descriptor,
        pendingSubmissions(store),
      )
    : new CodexTmuxAdapter(options);
}
export async function createFreshAdapter(
  directory: string,
  config: Config,
  store: Store,
): Promise<HarnessAdapter> {
  if (config.codex.adapter === "protocol")
    return protocolAdapter(directory, config, store);
  return new CodexTmuxAdapter(await tmuxOptions(directory, config, store));
}
export function describeAdapter(
  adapter: HarnessAdapter,
  sessionId: string,
): string {
  return JSON.stringify(
    adapter instanceof CodexTmuxAdapter
      ? { kind: "tmux", descriptor: adapter.snapshot() }
      : { kind: "protocol", sessionId },
  );
}
export function persistAdapter(
  adapter: HarnessAdapter,
  store: Store,
  sessionId?: string,
): void {
  if (adapter instanceof CodexTmuxAdapter) {
    store.setCursor(
      "adapter-descriptor",
      JSON.stringify({ kind: "tmux", descriptor: adapter.snapshot() }),
    );
  } else {
    const existing = store.getCursor("adapter-descriptor");
    if (sessionId)
      store.setCursor(
        "adapter-descriptor",
        JSON.stringify({ kind: "protocol", sessionId }),
      );
    else if (!existing)
      throw new Error("Protocol session id is required before persistence");
  }
}

export async function probeAdapterSearch(
  directory: string,
  config: Config,
  store: Store,
) {
  const adapter = await openAdapter(directory, config, store);
  try {
    await adapter.reconcile(store.getCursor("adapter-cursor"));
    if (!(adapter instanceof ProtocolAdapter)) {
      throw new Error(
        "This adapter cannot execute a search probe inside the agent context",
      );
    }
    const env = Object.fromEntries(
      Object.entries(process.env).filter(
        (entry): entry is [string, string] => entry[1] !== undefined,
      ),
    );
    return await adapter.commandExec(
      [
        "bash",
        "-lc",
        "command -v thc >/dev/null && thc search __thc_doctor_probe__ --limit 1",
      ],
      {
        cwd: directory,
        env,
        sandboxPolicy: sandboxPolicy(config.sandbox, directory),
      },
    );
  } finally {
    await adapter.close();
  }
}
