import { mkdir, mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { z } from "zod";
import type { Turn, TurnStatus } from "../../src/store/index.ts";
import {
  approvalCallback,
  assertDenied,
  assertOrderedAnswers,
  assertProtocolInputs,
  assertTmuxInputs,
  hasToolAttempt,
  hasRunningTool,
} from "./live-checks.ts";
import { inspect, openHistory, turnFor, waitFor } from "./live-runtime.ts";
import type { RunnerState } from "./live-runtime.ts";
import { setup, cleanup, releaseBarrier } from "./live-setup.ts";
import { saveFailure } from "./live-diagnostics.ts";
import type {
  LiveEnvironment,
  Resources,
  RestartEnvironment,
} from "./live-setup.ts";

async function waitForOwnedTurn(
  root: string,
  updateId: number,
  runnerState: RunnerState,
  statuses: readonly TurnStatus[],
): Promise<Turn> {
  return waitFor(
    () => {
      const turn = turnFor(inspect(root), updateId);
      return turn && statuses.includes(turn.status) ? turn : undefined;
    },
    `turn for update ${String(updateId)}`,
    runnerState,
  );
}
async function waitForAnswer(
  root: string,
  updateId: number,
  marker: string,
  runnerState: RunnerState,
): Promise<Turn> {
  return waitFor(
    () => {
      const state = inspect(root),
        turn = turnFor(state, updateId);
      return turn?.status === "completed" &&
        state?.messages.some(
          (row) =>
            row.turnId === turn.id &&
            row.author === "assistant" &&
            row.text.includes(marker),
        )
        ? turn
        : undefined;
    },
    `delivered answer for update ${String(updateId)}`,
    runnerState,
  );
}
export async function restartMidTurn(env: RestartEnvironment): Promise<Turn[]> {
  const { root, server, adapter, history, firstMarker, secondMarker, barrier } =
    env;
  const first = await waitForOwnedTurn(root, 1, env.running.state, ["active"]);
  await waitFor(
    async () => hasToolAttempt((await history.read()).tools, barrier),
    "first barrier command actually started",
    env.running.state,
  );
  server.input(
    2,
    `Reply with exactly ${secondMarker}; do not call tools or write files.`,
  );
  const second = await waitForOwnedTurn(root, 2, env.running.state, ["queued"]);
  if (
    turnFor(inspect(root), 1)?.status !== "active" ||
    !hasRunningTool(await history.read(), barrier)
  )
    throw new Error("First shell command finished before restart");
  await env.running.stop();
  if (
    turnFor(inspect(root), 1)?.status !== "active" ||
    !hasRunningTool(await history.read(), barrier)
  )
    throw new Error("First shell command did not remain active at restart");
  env.running = env.start();
  await waitFor(
    () => env.running.state.attached,
    "restarted bridge adapter",
    env.running.state,
  );
  if (
    turnFor(inspect(root), 1)?.status !== "active" ||
    !hasRunningTool(await history.read(), barrier)
  )
    throw new Error("First shell command did not remain active after restart");
  history.close();
  await releaseBarrier(barrier);
  await waitForAnswer(root, 1, firstMarker, env.running.state);
  await waitForAnswer(root, 2, secondMarker, env.running.state);
  const state = inspect(root);
  if (!state) throw new Error("Owned store disappeared");
  assertOrderedAnswers(state.messages, [
    { turnId: first.id, marker: firstMarker },
    { turnId: second.id, marker: secondMarker },
  ]);
  console.log(`${adapter}: queued follow-up and in-flight restart passed`);
  return [first, second];
}
async function permissionScenario(env: LiveEnvironment): Promise<Turn> {
  const { root, server, adapter, target, permissionMarker } = env;
  server.input(
    3,
    `Use your shell tool to run exactly: printf '%s' '${permissionMarker}' > '${target}'. This is a disposable file in this test workspace. ${adapter === "protocol" ? "Request permission if needed." : "Attempt it under the existing policy and do not request approval."} Then reply with ${permissionMarker}.`,
  );
  const turn = await waitForOwnedTurn(root, 3, env.running.state, ["active"]);
  if (adapter === "protocol") await approveOwnedRequest(env, turn);
  await waitForAnswer(root, 3, permissionMarker, env.running.state);
  if (adapter === "protocol") {
    if ((await readFile(target, "utf8")) !== permissionMarker)
      throw new Error("Approved test write did not complete");
  } else {
    const { existsSync } = await import("node:fs");
    if (existsSync(target))
      throw new Error("Forbidden tmux write unexpectedly succeeded");
  }
  console.log(
    `${adapter}: ${adapter === "protocol" ? "approval" : "sandbox denial"} passed`,
  );
  return turn;
}
async function approveOwnedRequest(
  env: LiveEnvironment,
  turn: Turn,
): Promise<void> {
  const card = await waitFor(
    () => {
      const state = inspect(env.root);
      const request = state?.requests.find(
        (row) =>
          row.turnId === turn.id &&
          row.kind === "approval" &&
          row.status === "pending" &&
          row.payload.includes(env.target),
      );
      const transient =
        request &&
        state?.transients.find(
          (row) => row.id === `request/${request.id}` && row.messageId !== null,
        );
      return request && transient?.messageId != null
        ? { request, messageId: transient.messageId }
        : undefined;
    },
    "actual approval request for the owned test file",
    env.running.state,
  );
  env.server.updates.push(
    approvalCallback(4, card.messageId, `p:${card.request.id}:a`),
  );
  await waitFor(
    () =>
      inspect(env.root)?.requests.some(
        (row) => row.id === card.request.id && row.status === "answered",
      ),
    "approval acknowledgement",
    env.running.state,
  );
}
async function assertOwnedHistory(
  env: LiveEnvironment,
  turns: readonly Turn[],
): Promise<void> {
  const state = inspect(env.root);
  if (!state) throw new Error("Owned store disappeared");
  const markers = [env.firstMarker, env.secondMarker, env.permissionMarker];
  const expected = turns.map((turn, index) => {
    const marker = markers[index];
    if (!marker) throw new Error("Missing expected marker");
    const raw = state.cursors[`tmux-submission/${turn.id}`];
    const parsed = raw
      ? z.object({ envelope: z.string() }).parse(JSON.parse(raw) as unknown)
      : undefined;
    return {
      turnId: turn.id,
      marker,
      ...(parsed ? { envelope: parsed.envelope } : {}),
    };
  });
  const history = await env.history.read();
  if (env.adapter === "protocol")
    assertProtocolInputs(history.items ?? [], expected);
  else assertTmuxInputs(history.records ?? [], expected);
}
async function exercise(
  env: LiveEnvironment,
  resources: Resources,
  buildRoot: string,
): Promise<void> {
  try {
    const turns = await restartMidTurn(env);
    const permissionTurn = await permissionScenario(env);
    env.history = await openHistory(
      buildRoot,
      env.root,
      env.config,
      env.adapter,
      inspect(env.root),
    );
    resources.history = env.history;
    if (env.adapter === "tmux")
      assertDenied((await env.history.read()).tools, env.target);
    await assertOwnedHistory(env, [...turns, permissionTurn]);
    console.log(`${env.adapter}: three owned inputs occurred exactly once`);
  } finally {
    resources.running = env.running;
  }
}
export async function acceptance(
  adapter: "protocol" | "tmux",
  buildRoot: string,
): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), "thc-live-"));
  const resources: Resources = { root, owned: new Map(), started: false };
  try {
    await mkdir(join(root, ".thc"));
    const env = await setup(resources, buildRoot, adapter);
    await exercise(env, resources, buildRoot);
  } catch (error) {
    try {
      const directory = await saveFailure(resources, error);
      console.error(`Live failure diagnostics: ${directory}`);
    } catch (diagnosticError) {
      console.error(
        `Live diagnostics failed: ${diagnosticError instanceof Error ? diagnosticError.message : "unknown error"}`,
      );
    }
    throw error;
  } finally {
    await cleanup(resources);
  }
}
