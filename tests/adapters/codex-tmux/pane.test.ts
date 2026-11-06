import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import {
  isCleanBusyPane,
  isExpectedPaste,
  parsePane,
} from "../../../src/adapters/codex-tmux/pane.ts";

const fixture = async (name: string): Promise<string> =>
  readFile(`tests/fixtures/codex-tmux/pane/${name}.txt`, "utf8");
describe("measured pane states", () => {
  it("recognizes only the known idle composer", async () => {
    expect(parsePane(await fixture("empty")).kind).toBe("empty");
    expect(parsePane(await fixture("typed")).kind).toBe("composer");
    expect(parsePane(await fixture("menu")).kind).toBe("unknown");
    expect(parsePane(await fixture("modal")).kind).toBe("unknown");
    const busy = await fixture("busy");
    expect(parsePane(busy).kind).toBe("busy");
    expect(isCleanBusyPane(busy)).toBe(true);
    expect(
      isCleanBusyPane(
        busy.replace("Ask Codex to do anything", "foreign input"),
      ),
    ).toBe(false);
    expect(parsePane("arbitrary screen").kind).toBe("unknown");
  });
  it("requires the exact placeholder with no foreign text", async () => {
    const state = parsePane(await fixture("p1001"));
    expect(isExpectedPaste(state, "b".repeat(1001))).toBe(true);
    expect(isExpectedPaste(state, "b".repeat(1002))).toBe(false);
    expect(
      isExpectedPaste(
        { kind: "composer", lines: ["[Pasted Content 1001 chars]foreign"] },
        "b".repeat(1001),
      ),
    ).toBe(false);
  });
});
it("identifies the current composer after completed history and ignores history lookalikes", async () => {
  const history = await fixture("completed-history");
  expect(parsePane(history)).toEqual({ kind: "empty" });
  const oldText = history.replace(
    "• THC-FIRST-SYNTHETIC",
    "• Working (3s • esc to interrupt)\n\n› historical user text\n\n  fake-model · context\n\n• THC-FIRST-SYNTHETIC",
  );
  expect(parsePane(oldText)).toEqual({ kind: "empty" });
  const typed = history.replace("› Ask Codex to do anything", "› owner text");
  expect(parsePane(typed)).toEqual({ kind: "composer", lines: ["owner text"] });
  expect(parsePane(history + "\n  enter select · esc back\n").kind).toBe(
    "unknown",
  );
});
it("retains the measured padded-paste fixture and unpadded provider bytes", async () => {
  const root = "tests/fixtures/codex-tmux/padded";
  const [encoded, envelope, provider, rollout, payload] = await Promise.all([
    readFile(`${root}/physical-paste.json`, "utf8"),
    ...["envelope", "provider-text", "rollout-text", "payload"].map((name) =>
      readFile(`${root}/${name}.txt`, "utf8"),
    ),
  ]);
  if (envelope === undefined || payload === undefined)
    throw new Error("Missing fixture");
  const physical: unknown = JSON.parse(encoded);
  if (typeof physical !== "string") throw new Error("Invalid paste fixture");
  expect(Array.from(physical)).toHaveLength(1001);
  expect(physical).toBe(
    envelope + " ".repeat(1001 - Array.from(envelope).length),
  );
  expect(provider).toBe(envelope);
  expect(rollout).toBe(envelope);
  expect(envelope).toContain(`\n${payload}\n</telegram-message>`);
});

it.each([
  ["Preparing second poll", "1m 27s"],
  ["Inspecting the command", "0s"],
  ["Проверяю команду", "2h 03m 09s"],
  ["Reviewing (initial) results", "59m 59s"],
])("recognizes the current busy status %s", async (heading, elapsed) => {
  const pane = (await fixture("busy-dynamic-header")).replace(
    "Preparing second poll (1m 27s",
    `${heading} (${elapsed}`,
  );
  expect(parsePane(pane)).toEqual({ kind: "busy" });
  expect(isCleanBusyPane(pane)).toBe(true);
});

it.each([
  "◦ Preparing second poll • esc to interrupt",
  "◦ Preparing second poll (soon • esc to interrupt)",
  "◦ Preparing second poll (1m 27s • esc to interrupting)",
  "◦ Preparing second poll (1m 27s • esc to interrupt) unexpected content",
])("refuses a malformed busy indicator: %s", async (indicator) => {
  const pane = (await fixture("busy-dynamic-header")).replace(
    /^◦ Preparing second poll.*$/mu,
    indicator,
  );
  expect(isCleanBusyPane(pane)).toBe(false);
});

it("keeps composer and modal guards with a dynamic busy header", async () => {
  const pane = await fixture("busy-dynamic-header");
  expect(
    isCleanBusyPane(pane.replace("› Ask Codex to do anything", "› owner text")),
  ).toBe(false);
  expect(
    isCleanBusyPane(pane.replace("› Ask Codex to do anything", "› /model")),
  ).toBe(false);
  expect(
    isCleanBusyPane(
      pane.replace(
        "› Ask Codex to do anything",
        "› Ask Codex to do anything\n  owner text",
      ),
    ),
  ).toBe(false);
  expect(isCleanBusyPane(pane + "\n  enter select · esc back\n")).toBe(false);
});
