import { expect, it } from "vitest";
import { catalog } from "../../src/channels/index.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { registerMenus } from "../../src/core/runtime-menu.js";

function menuFixture() {
  const calls: {
    commands: { command: string; description: string }[];
    language?: string;
    scope?: string;
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
            options: { language_code?: string; scope?: { type: string } },
            signal: AbortSignal,
          ) => {
            calls.push({
              commands,
              ...(options.scope ? { scope: options.scope.type } : {}),
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
  return { calls, controller, ctx };
}

it("registers a default English menu as well as English and Russian overrides", async () => {
  const { calls, controller, ctx } = menuFixture();
  await registerMenus(ctx);
  expect(calls.map((call) => [call.scope, call.language])).toEqual([
    [undefined, undefined],
    [undefined, "en"],
    [undefined, "ru"],
    ["all_private_chats", undefined],
    ["all_private_chats", "en"],
    ["all_private_chats", "ru"],
  ]);
  for (const call of calls) {
    expect(call.commands.map(({ command }) => command)).toEqual([
      "stop",
      "status",
      "new",
      "mode",
      "compact",
      "format",
      "help",
    ]);
    expect(call.signal.aborted).toBe(false);
  }
  expect(calls[0]?.commands[0]?.description).toBe(catalog.en.stop);
  expect(calls[2]?.commands[0]?.description).toBe(catalog.ru.stop);
  controller.abort();
  expect(calls.every((call) => call.signal.aborted)).toBe(true);
});

it("replaces a legacy private-chat menu that overrides the global menu", async () => {
  const menus = new Map<string, string[]>([
    ["all_private_chats/", ["start", "help", "status"]],
  ]);
  const controller = new AbortController();
  const ctx = {
    signal: controller.signal,
    log: () => undefined,
    channel: {
      bot: {
        api: {
          setMyCommands: (
            commands: { command: string }[],
            options: { scope?: { type: string }; language_code?: string },
          ) => {
            menus.set(
              `${options.scope?.type ?? "default"}/${options.language_code ?? ""}`,
              commands.map(({ command }) => command),
            );
            return Promise.resolve(true);
          },
        },
      },
    },
  } as unknown as RuntimeContext;
  await registerMenus(ctx);
  const effective = menus.get("all_private_chats/") ?? menus.get("default/");
  expect(effective).toEqual([
    "stop",
    "status",
    "new",
    "mode",
    "compact",
    "format",
    "help",
  ]);
});
