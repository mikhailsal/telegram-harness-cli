import { existsSync } from "node:fs";
import { readFile, realpath } from "node:fs/promises";
import { join, relative, isAbsolute } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { pathToFileURL } from "node:url";
import { z } from "zod";
import type { Config } from "../../src/core/config.ts";
import type {
  Inbox,
  Turn,
  VisibleMessage,
  PendingRequest,
  Transient,
} from "../../src/store/index.ts";
import type { ProtocolTransport } from "../../src/adapters/codex-protocol/transport.ts";
import { ProtocolCommandMonitor } from "./live-checks.ts";
import type { HistorySnapshot } from "./live-checks.ts";

const descriptorSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("protocol"), sessionId: z.string() }).loose(),
  z
    .object({
      kind: z.literal("tmux"),
      descriptor: z
        .object({
          cwd: z.string(),
          rolloutPath: z.string(),
          threadId: z.string(),
          originator: z.string(),
        })
        .loose(),
    })
    .loose(),
]);
const pageSchema = z.object({
  data: z.array(z.object({ items: z.array(z.unknown()) })),
  nextCursor: z.string().nullish(),
});
type Descriptor = z.infer<typeof descriptorSchema>;
export interface LiveState {
  inbox: Inbox[];
  turns: Turn[];
  messages: VisibleMessage[];
  requests: PendingRequest[];
  transients: Transient[];
  cursors: Record<string, string>;
}
export interface LiveHistory {
  read(): Promise<HistorySnapshot>;
  close(): void;
}
export interface RunnerState {
  error?: Error;
  attached?: boolean;
}
export function inspect(root: string): LiveState | undefined {
  const path = join(root, ".thc", "store.db");
  if (!existsSync(path)) return undefined;
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    const query = (sql: string) => db.prepare(sql).all();
    const meta = query("SELECT name,value FROM meta") as unknown as {
      name: string;
      value: string;
    }[];
    return {
      inbox: query(
        "SELECT * FROM inbox ORDER BY updateId",
      ) as unknown as Inbox[],
      turns: query(
        "SELECT * FROM turns ORDER BY createdAt,id",
      ) as unknown as Turn[],
      messages: query(
        "SELECT * FROM messages ORDER BY rowid",
      ) as unknown as VisibleMessage[],
      requests: query(
        "SELECT * FROM pending_requests ORDER BY createdAt,id",
      ) as unknown as PendingRequest[],
      transients: query(
        "SELECT * FROM transient ORDER BY createdAt,id",
      ) as unknown as Transient[],
      cursors: Object.fromEntries(meta.map((row) => [row.name, row.value])),
    };
  } catch (error) {
    if (error instanceof Error && error.message.includes("no such table"))
      return undefined;
    throw error;
  } finally {
    db.close();
  }
}
export function turnFor(
  state: LiveState | undefined,
  updateId: number,
): Turn | undefined {
  const row = state?.inbox.find((item) => item.updateId === updateId);
  return state?.turns.find((item) => item.id === row?.turnId);
}
export async function waitFor<T>(
  check: () => T | undefined | false | Promise<T | undefined | false>,
  label: string,
  state: RunnerState,
  timeout = 120000,
): Promise<T> {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (state.error) throw state.error;
    const value = await check();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Live acceptance timed out: ${label}`);
}
function ownedDescriptor(
  state: LiveState | undefined,
  root: string,
): Descriptor | undefined {
  const raw = state?.cursors["adapter-descriptor"];
  if (!raw) return undefined;
  const descriptor = descriptorSchema.parse(JSON.parse(raw) as unknown);
  if (descriptor.kind === "tmux" && descriptor.descriptor.cwd !== root)
    throw new Error("Tmux descriptor does not belong to the test workspace");
  return descriptor;
}
export async function openHistory(
  buildRoot: string,
  root: string,
  config: Config,
  adapter: "protocol" | "tmux",
  state: LiveState | undefined,
): Promise<LiveHistory> {
  const descriptor = ownedDescriptor(state, root);
  if (!descriptor) throw new Error("Owned adapter descriptor is missing");
  if (adapter === "protocol")
    return protocolHistory(buildRoot, root, config, descriptor);
  return tmuxHistory(buildRoot, root, config, descriptor);
}
async function protocolHistory(
  buildRoot: string,
  root: string,
  config: Config,
  descriptor: Descriptor,
): Promise<LiveHistory> {
  if (descriptor.kind !== "protocol")
    throw new Error("Unexpected protocol descriptor");
  const configModule = (await import(
    pathToFileURL(join(buildRoot, "dist/core/config.js")).href
  )) as typeof import("../../src/core/config.ts");
  const transportModule = (await import(
    pathToFileURL(join(buildRoot, "dist/adapters/codex-protocol/transport.js"))
      .href
  )) as typeof import("../../src/adapters/codex-protocol/transport.ts");
  const monitor = new ProtocolCommandMonitor(descriptor.sessionId);
  const transport = new transportModule.ProtocolTransport(
    (message) => {
      monitor.receive(message);
    },
    () => {
      /* Disconnection is reported by the next call. */
    },
  );
  await transport.connect(
    config.codex.socket ??
      join(
        configModule.codexHome(config),
        "app-server-control",
        "app-server-control.sock",
      ),
  );
  try {
    await transport.call("thread/resume", {
      threadId: descriptor.sessionId,
      cwd: root,
      sandbox: config.sandbox,
      approvalPolicy: "on-request",
    });
  } catch (error) {
    transport.close();
    throw error;
  }
  return {
    read: () => readProtocol(transport, descriptor.sessionId, monitor),
    close: () => {
      transport.close();
    },
  };
}
export async function readProtocol(
  transport: Pick<ProtocolTransport, "call">,
  sessionId: string,
  monitor: ProtocolCommandMonitor,
): Promise<HistorySnapshot> {
  const items: unknown[] = [];
  let cursor: string | undefined;
  do {
    let page: z.infer<typeof pageSchema>;
    try {
      page = pageSchema.parse(
        await transport.call("thread/turns/list", {
          threadId: sessionId,
          itemsView: "full",
          sortDirection: "asc",
          limit: 100,
          ...(cursor ? { cursor } : {}),
        }),
      );
    } catch (error) {
      if (
        error instanceof Error &&
        error.message === "list_turns is not supported yet"
      )
        return { items: [], tools: monitor.active() };
      throw error;
    }
    for (const turn of page.data) items.push(...turn.items);
    cursor = page.nextCursor ?? undefined;
  } while (cursor);
  return { items, tools: monitor.active() };
}
async function tmuxHistory(
  buildRoot: string,
  root: string,
  config: Config,
  descriptor: Descriptor,
): Promise<LiveHistory> {
  if (descriptor.kind !== "tmux") throw new Error("Unexpected tmux descriptor");
  const info = descriptor.descriptor;
  if (!info.rolloutPath || !info.threadId)
    throw new Error("Owned tmux rollout is not available");
  const configModule = (await import(
    pathToFileURL(join(buildRoot, "dist/core/config.js")).href
  )) as typeof import("../../src/core/config.ts");
  const filesModule = (await import(
    pathToFileURL(join(buildRoot, "dist/adapters/codex-tmux/files.js")).href
  )) as typeof import("../../src/adapters/codex-tmux/files.ts");
  const directory = await realpath(
    join(configModule.codexHome(config), "sessions"),
  );
  const path = await realpath(info.rolloutPath),
    inside = relative(directory, path);
  if (!inside || inside.startsWith("..") || isAbsolute(inside))
    throw new Error("Rollout is outside the Codex sessions directory");
  if (
    (await filesModule.rolloutIdentity(path, root, info.originator)) !==
    info.threadId
  )
    throw new Error("Tmux rollout identity does not match the owned session");
  return {
    read: () => readTmux(path),
    close() {
      /* No persistent handle. */
    },
  };
}
async function readTmux(path: string): Promise<HistorySnapshot> {
  const text = await readFile(path, "utf8");
  const records: unknown[] = text
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as unknown);
  const tools = records.filter((record) => {
    const item = z
      .object({ payload: z.object({ type: z.string() }).loose() })
      .loose()
      .safeParse(record);
    return (
      item.success &&
      [
        "function_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "exec_command_begin",
        "exec_command_end",
      ].includes(item.data.payload.type)
    );
  });
  return { records, tools };
}
