import { test, vi } from "vitest";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { restartMidTurn } from "./live-scenario.ts";
import { cleanup, pinSettings, waitForHistory } from "./live-setup.ts";
import type { CodexTmuxAdapter } from "../../src/adapters/codex-tmux/index.ts";
import type {
  AdapterInput,
  HarnessAdapter,
} from "../../src/adapters/contract.ts";

function fakeHistory(finishAt: number, barrier: string) {
  let reads = 0;
  const history = {
    read: () => {
      reads++;
      return Promise.resolve({
        items: [],
        tools: [
          {
            type: "commandExecution",
            command: `while waiting for ${barrier}`,
            status: reads >= finishAt ? "completed" : "inProgress",
          },
        ],
      });
    },
    close() {
      /* No live handle in this fixture. */
    },
  };
  return history;
}
function fakeServer(db: DatabaseSync) {
  const server = {
    input(id: number) {
      assert.equal(id, 2);
      db.exec(
        "INSERT INTO inbox VALUES(2, 'two'); INSERT INTO turns VALUES('two', 'queued', '2026-01-02')",
      );
    },
  };
  return server;
}
async function fixture(finishAt = Infinity) {
  const root = await mkdtemp(join(tmpdir(), "thc-live-fixture-"));
  await mkdir(join(root, ".thc"));
  const db = new DatabaseSync(join(root, ".thc", "store.db"));
  db.exec(
    `CREATE TABLE meta(name TEXT, value TEXT); CREATE TABLE inbox(updateId INTEGER, turnId TEXT); CREATE TABLE turns(id TEXT, status TEXT, createdAt TEXT); CREATE TABLE messages(author TEXT, turnId TEXT, text TEXT); CREATE TABLE pending_requests(id TEXT, createdAt TEXT); CREATE TABLE transient(id TEXT, createdAt TEXT); INSERT INTO inbox VALUES(1, 'one'); INSERT INTO turns VALUES('one', 'active', '2026-01-01');`,
  );
  let stops = 0,
    restarts = 0;
  const barrier = join(root, "release-first-turn");
  const history = fakeHistory(finishAt, barrier);
  let timer: ReturnType<typeof setInterval> | undefined;
  const server = fakeServer(db);
  const running = {
    state: {},
    stop() {
      stops++;
      return Promise.resolve();
    },
  };
  const start = () => {
    restarts++;
    assert.equal(existsSync(barrier), false);
    timer = setInterval(() => {
      if (!existsSync(barrier)) return;
      clearInterval(timer);
      db.exec(
        "UPDATE turns SET status='completed'; INSERT INTO messages VALUES('assistant', 'one', 'first marker'); INSERT INTO messages VALUES('assistant', 'two', 'second marker')",
      );
    }, 10);
    return { state: { attached: true }, stop: () => Promise.resolve() };
  };
  return {
    env: {
      root,
      server,
      adapter: "protocol" as const,
      history,
      running,
      start,
      firstMarker: "first marker",
      secondMarker: "second marker",
      barrier,
    },
    counts: () => ({ stops, restarts }),
    async close() {
      clearInterval(timer);
      db.close();
      await rm(root, { recursive: true, force: true });
    },
  };
}
test("queues a second turn, restarts mid-command, and verifies ordered delivery", async () => {
  const f = await fixture();
  try {
    const turns = await restartMidTurn(f.env);
    assert.deepEqual(
      turns.map((turn) => turn.id),
      ["one", "two"],
    );
    assert.deepEqual(f.counts(), { stops: 1, restarts: 1 });
    assert.equal(await readFile(f.env.barrier, "utf8"), "release\n");
  } finally {
    await f.close();
  }
});
test("waits for the owned thread's history and propagates unrelated observer errors", async () => {
  let attempts = 0;
  const history = {
    read: () => Promise.resolve({ tools: [] }),
    close() {
      /* No handle. */
    },
  };
  assert.equal(
    await waitForHistory(() => {
      attempts++;
      if (attempts === 1)
        return Promise.reject(
          new Error("no rollout found for thread id owned"),
        );
      if (attempts === 2)
        return Promise.reject(new Error("list_turns is not supported yet"));
      return Promise.resolve(history);
    }, {}),
    history,
  );
  assert.equal(attempts, 3);
  await assert.rejects(
    waitForHistory(() => Promise.reject(new Error("permission denied")), {}),
    /permission denied/,
  );
});
test("a lasting history refusal still fails, and a bridge failure comes first", async () => {
  const refusal = () =>
    Promise.reject(new Error("list_turns is not supported yet"));
  await assert.rejects(
    waitForHistory(refusal, { error: new Error("bridge failed") }),
    /bridge failed/,
  );
  vi.useFakeTimers();
  try {
    const waiting = assert.rejects(
      waitForHistory(refusal, {}),
      /timed out: owned thread rollout to materialize/,
    );
    await vi.advanceTimersByTimeAsync(121000);
    await waiting;
  } finally {
    vi.useRealTimers();
  }
});
test("cleanup releases the owned barrier before stopping the bridge", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-live-cleanup-"));
  const barrier = join(root, "release-first-turn");
  let stopped = false;
  await cleanup({
    root,
    barrier,
    owned: new Map(),
    started: false,
    running: {
      state: {},
      async stop() {
        assert.equal(await readFile(barrier, "utf8"), "release\n");
        stopped = true;
      },
    },
  });
  assert.equal(stopped, true);
  assert.equal(existsSync(root), false);
});
test("fails rather than claiming a mid-turn restart after the command finished", async () => {
  const f = await fixture(3);
  try {
    await assert.rejects(restartMidTurn(f.env), /did not remain active/);
    assert.deepEqual(f.counts(), { stops: 1, restarts: 0 });
  } finally {
    await f.close();
  }
});
test("requires the shell command to remain active after the new bridge attaches", async () => {
  const f = await fixture(4);
  try {
    await assert.rejects(
      restartMidTurn(f.env),
      /did not remain active after restart/,
    );
    assert.deepEqual(f.counts(), { stops: 1, restarts: 1 });
    assert.equal(existsSync(f.env.barrier), false);
  } finally {
    await f.close();
  }
});

test("pins the run's model and effort on both adapters", async () => {
  class FakeTmux {
    options: { settings?: object } = {};
  }
  const tmuxClass = FakeTmux as unknown as typeof CodexTmuxAdapter;
  const tmux = new FakeTmux();
  pinSettings(tmux as unknown as HarnessAdapter, { effort: "low" }, tmuxClass);
  assert.deepEqual(tmux.options.settings, { effort: "low" });

  const sent: AdapterInput[] = [];
  const protocol = {
    submit(input: AdapterInput) {
      sent.push(input);
      return Promise.resolve({});
    },
  } as unknown as HarnessAdapter;
  pinSettings(protocol, { model: "gpt-test", effort: "low" }, tmuxClass);
  await protocol.submit({ correlationId: "a", text: "one" });
  await protocol.submit({
    correlationId: "b",
    text: "two",
    settings: { effort: "high" },
  });
  assert.deepEqual(
    sent.map((input) => input.settings),
    [
      { model: "gpt-test", effort: "low" },
      { model: "gpt-test", effort: "high" },
    ],
  );
});
