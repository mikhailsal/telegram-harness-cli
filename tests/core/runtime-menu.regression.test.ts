import { expect, it } from "vitest";
import { catalog } from "../../src/channels/index.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { registerMenus } from "../../src/core/runtime-menu.js";

it("registers a default English menu as well as English and Russian overrides", async () => {
  const calls: {
    commands: { command: string; description: string }[];
    language?: string;
    signal: AbortSignal;
  }[] = [];
  const controller = new AbortController();
  const ctx = {
    signal: controller.signal,
    log: () => undefined,
    channel: {
      bot: {
        api: {
          setMyCommands: (
            commands: { command: string; description: string }[],
            options: { language_code?: string },
            signal: AbortSignal,
          ) => {
            calls.push({
              commands,
              ...(options.language_code
                ? { language: options.language_code }
                : {}),
              signal,
            });
            return Promise.resolve(true);
          },
        },
      },
    },
  } as unknown as RuntimeContext;
  await registerMenus(ctx);
  expect(calls.map((call) => call.language)).toEqual([undefined, "en", "ru"]);
  for (const call of calls) {
    expect(call.commands.map(({ command }) => command)).toEqual([
      "stop",
      "status",
      "new",
      "mode",
      "compact",
      "help",
    ]);
    expect(call.signal.aborted).toBe(false);
  }
  expect(calls[0]?.commands[0]?.description).toBe(catalog.en.stop);
  expect(calls[2]?.commands[0]?.description).toBe(catalog.ru.stop);
  controller.abort();
  expect(calls.every((call) => call.signal.aborted)).toBe(true);
});
