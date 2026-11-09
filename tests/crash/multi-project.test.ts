import { join } from "node:path";
import { expect, it } from "vitest";
import { Store } from "../../src/store/index.js";
import { fixture, waitUntil } from "../fakes/runtime-fixture.js";

type Project = Awaited<ReturnType<typeof fixture>>;
async function complete(
  project: Project,
  correlationId: string,
  text: string,
  turnId = "turn-1",
) {
  project.server.event("turn-completed", turnId, {
    correlationId,
    text,
    data: { status: "completed" },
  });
  await waitUntil(() =>
    JSON.stringify(project.server.delivered).includes(text),
  );
}
function assertStoredInput(project: Project, bot: string, task: string) {
  const store = new Store(join(project.root, ".thc", "store.db"), true);
  try {
    const entityId = store.getCursor("entity-id");
    expect(entityId && store.getEntity(entityId)?.botId).toBe(bot);
    expect(store.getInbox(1)?.payload).toContain(task);
  } finally {
    store.close();
  }
}

it("isolates two bot projects and keeps the survivor working after one stops", async () => {
  const first = await fixture("123");
  const second = await fixture("456");
  first.server.input(1, "first project task");
  second.server.input(1, "second project task");
  first.start();
  second.start();
  await waitUntil(
    () =>
      first.server.submissions.length === 1 &&
      second.server.submissions.length === 1,
  );
  const firstId = first.server.submissions[0]?.correlationId;
  const secondId = second.server.submissions[0]?.correlationId;
  if (!firstId || !secondId) throw new Error("Missing task correlation");
  expect(first.server.submissions[0]?.text).toContain("first project task");
  expect(second.server.submissions[0]?.text).toContain("second project task");
  await complete(first, firstId, "first project answer");
  await complete(second, secondId, "second project answer");
  expect(JSON.stringify(first.server.delivered)).not.toContain(
    "second project answer",
  );
  expect(JSON.stringify(second.server.delivered)).not.toContain(
    "first project answer",
  );
  assertStoredInput(first, "123", "first project task");
  assertStoredInput(second, "456", "second project task");
  await first.kill();
  second.server.input(2, "surviving project task");
  await waitUntil(() => second.server.submissions.length === 2);
  const nextId = second.server.submissions[1]?.correlationId;
  if (!nextId) throw new Error("Missing follow-up correlation");
  await complete(second, nextId, "survivor answer", "turn-2");
  expect(second.server.submissions[1]?.text).toContain(
    "surviving project task",
  );
  expect(first.server.submissions).toHaveLength(1);
  expect(JSON.stringify(first.server.delivered)).not.toContain(
    "survivor answer",
  );
}, 30000);
