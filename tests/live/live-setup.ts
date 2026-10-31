import { spawnSync } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { z } from "zod";
import type { Config } from "../../src/core/config.ts";
import type { RuntimeContext } from "../../src/core/context.ts";
import type { startBridge as startBridgeType } from "../../src/core/runtime.ts";
import type { openAdapter as openAdapterType } from "../../src/core/adapter.ts";
import type { CodexTmuxAdapter } from "../../src/adapters/codex-tmux/index.ts";
import { FakeServices } from "../fakes/servers.ts";
import { inspect, openHistory, turnFor, waitFor } from "./live-runtime.ts";
import type { LiveState, LiveHistory, RunnerState } from "./live-runtime.ts";

interface Running {
  state: RunnerState;
  stop(): Promise<void>;
}
export interface RestartEnvironment {
  root: string;
  server: Pick<FakeServices, "input">;
  adapter: "protocol" | "tmux";
  history: LiveHistory;
  running: Running;
  start(): Running;
  firstMarker: string;
  secondMarker: string;
  barrier: string;
}
export interface LiveEnvironment extends RestartEnvironment {
  server: FakeServices;
  config: Config;
  owned: Map<string, CodexTmuxAdapter>;
  started: boolean;
  permissionMarker: string;
  target: string;
}
export interface Resources {
  root: string;
  owned: Map<string, CodexTmuxAdapter>;
  server?: FakeServices;
  running?: Running;
  history?: LiveHistory;
  started: boolean;
  barrier?: string;
}
export async function releaseBarrier(path: string): Promise<void> {
  try {
    await writeFile(path, "release\n", { flag: "wx" });
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "EEXIST")
      return;
    throw error;
  }
}
interface RuntimeModules {
  startBridge: typeof startBridgeType;
  openAdapter: typeof openAdapterType;
  TmuxAdapter: typeof CodexTmuxAdapter;
  parseConfig: typeof import("../../src/core/config.ts").parseConfig;
}
const descriptorReadySchema = z
  .object({
    kind: z.string(),
    sessionId: z.string().optional(),
    descriptor: z
      .object({ rolloutPath: z.string(), threadId: z.string() })
      .loose()
      .optional(),
  })
  .loose();

function descriptorReady(
  state: LiveState | undefined,
  adapter: "protocol" | "tmux",
): boolean {
  const raw = state?.cursors["adapter-descriptor"];
  if (!raw) return false;
  const parsed = descriptorReadySchema.safeParse(JSON.parse(raw) as unknown);
  if (!parsed.success) return false;
  return adapter === "protocol"
    ? Boolean(parsed.data.sessionId)
    : Boolean(
        parsed.data.descriptor?.rolloutPath && parsed.data.descriptor.threadId,
      );
}
function runner(
  root: string,
  config: Config,
  server: FakeServices,
  runtime: RuntimeModules,
  owned: Map<string, CodexTmuxAdapter>,
): Running {
  const controller = new AbortController(),
    state: RunnerState = {};
  const run = runtime
    .startBridge(root, config, {
      apiRoot: server.url,
      signal: controller.signal,
      tickIntervalMs: 100,
      adapterFactory: async (ctx: RuntimeContext) => {
        const adapter = await runtime.openAdapter(
          ctx.directory,
          ctx.config,
          ctx.store,
        );
        if (adapter instanceof runtime.TmuxAdapter)
          owned.set(adapter.server, adapter);
        state.attached = true;
        return adapter;
      },
    })
    .catch((error: unknown) => {
      state.error = error instanceof Error ? error : new Error("Bridge failed");
    });
  return {
    state,
    async stop() {
      controller.abort();
      await run;
      if (state.error) throw state.error;
    },
  };
}
function terminateOwnedTmux(owned: Map<string, CodexTmuxAdapter>): void {
  for (const [name] of owned) {
    if (!/^thc-[0-9a-f-]{36}$/.test(name))
      throw new Error("Refusing to terminate an unrecognized tmux server");
    const result = spawnSync("tmux", ["-L", name, "kill-server"], {
      encoding: "utf8",
      timeout: 10000,
    });
    if (result.error) throw result.error;
    const remaining = spawnSync("tmux", ["-L", name, "has-session"], {
      timeout: 10000,
    });
    if (remaining.status === 0)
      throw new Error("Owned tmux server did not terminate");
  }
}
async function loadRuntime(buildRoot: string): Promise<
  RuntimeModules & {
    parseConfig: typeof import("../../src/core/config.ts").parseConfig;
  }
> {
  const runtime = (await import(
    pathToFileURL(join(buildRoot, "dist/core/runtime.js")).href
  )) as typeof import("../../src/core/runtime.ts");
  const config = (await import(
    pathToFileURL(join(buildRoot, "dist/core/config.js")).href
  )) as typeof import("../../src/core/config.ts");
  const adapter = (await import(
    pathToFileURL(join(buildRoot, "dist/core/adapter.js")).href
  )) as typeof import("../../src/core/adapter.ts");
  const tmux = (await import(
    pathToFileURL(join(buildRoot, "dist/adapters/codex-tmux/index.js")).href
  )) as typeof import("../../src/adapters/codex-tmux/index.ts");
  return {
    startBridge: runtime.startBridge,
    parseConfig: config.parseConfig,
    openAdapter: adapter.openAdapter,
    TmuxAdapter: tmux.CodexTmuxAdapter,
  };
}
function markers(): {
  firstMarker: string;
  secondMarker: string;
  permissionMarker: string;
} {
  const nonce = `${String(Date.now())}-${Math.random().toString(36).slice(2)}`;
  return {
    firstMarker: `THC-FIRST-${nonce}`,
    secondMarker: `THC-SECOND-${nonce}`,
    permissionMarker: `THC-PERMISSION-${nonce}`,
  };
}
function liveConfig(
  runtime: RuntimeModules,
  adapter: "protocol" | "tmux",
): Config {
  return runtime.parseConfig({
    token: { env: "THC_LIVE_SYNTHETIC_TOKEN" },
    allowlist: [1],
    sandbox: "read-only",
    status: "off",
    update_check: false,
    codex: { adapter, daemon_autostart: false },
    submit_ack_timeout: 45,
  });
}
export async function waitForHistory(
  open: () => Promise<LiveHistory>,
  state: RunnerState,
): Promise<LiveHistory> {
  return waitFor(
    async () => {
      try {
        return await open();
      } catch (error) {
        if (
          error instanceof Error &&
          error.message.startsWith("no rollout found for thread id ")
        )
          return undefined;
        throw error;
      }
    },
    "owned thread rollout to materialize",
    state,
  );
}
export async function setup(
  resources: Resources,
  buildRoot: string,
  adapter: "protocol" | "tmux",
): Promise<LiveEnvironment> {
  const server = new FakeServices();
  resources.server = server;
  await server.start();
  resources.started = true;
  const runtime = await loadRuntime(buildRoot);
  const config = liveConfig(runtime, adapter);
  const start = () =>
    runner(resources.root, config, server, runtime, resources.owned);
  const { firstMarker, secondMarker, permissionMarker } = markers();
  const barrier = join(resources.root, "release-first-turn");
  resources.barrier = barrier;
  const prompt = `Use your shell tool to run exactly: n=0; while [ ! -f '${barrier}' ]; do n=$((n+1)); [ "$n" -lt 240 ] || exit 1; sleep 1; done; printf '%s' '${firstMarker}'. Wait for the command, then reply with ${firstMarker}. Do not write files.`;
  const running = start();
  resources.running = running;
  server.input(1, prompt);
  const history = await materializedHistory(
    resources,
    buildRoot,
    adapter,
    config,
    running,
  );
  resources.history = history;
  return {
    root: resources.root,
    adapter,
    owned: resources.owned,
    started: true,
    server,
    config,
    start,
    running,
    history,
    firstMarker,
    secondMarker,
    barrier,
    permissionMarker,
    target: join(resources.root, "approved-test.txt"),
  };
}
async function materializedHistory(
  resources: Resources,
  buildRoot: string,
  adapter: "protocol" | "tmux",
  config: Config,
  running: Running,
): Promise<LiveHistory> {
  await waitFor(
    () => {
      const state = inspect(resources.root);
      return (
        descriptorReady(state, adapter) &&
        turnFor(state, 1)?.status === "active"
      );
    },
    "materialized owned session with an active first turn",
    running.state,
  );
  return waitForHistory(
    () =>
      openHistory(
        buildRoot,
        resources.root,
        config,
        adapter,
        inspect(resources.root),
      ),
    running.state,
  );
}
export async function cleanup(resources: Resources): Promise<void> {
  try {
    if (resources.barrier) await releaseBarrier(resources.barrier);
  } finally {
    await cleanupResources(resources);
  }
}
async function cleanupResources(resources: Resources): Promise<void> {
  try {
    if (resources.running) await resources.running.stop();
  } finally {
    resources.history?.close();
    try {
      terminateOwnedTmux(resources.owned);
    } finally {
      if (resources.started) await resources.server?.close();
      await rm(resources.root, { recursive: true, force: true });
    }
  }
}
