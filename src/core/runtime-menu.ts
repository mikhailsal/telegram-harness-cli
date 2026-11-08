import { catalog } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";

export async function registerMenus(ctx: RuntimeContext): Promise<void> {
  try {
    for (const scope of [undefined, { type: "all_private_chats" }] as const)
      for (const language of [undefined, "en", "ru"] as const) {
        const text = catalog[language ?? "en"];
        const commands = [
          "stop",
          "status",
          "new",
          "mode",
          "compact",
          "format",
          "help",
        ] as const;
        const signal = AbortSignal.any([
          ctx.signal,
          AbortSignal.timeout(30000),
        ]);
        await ctx.channel.bot.api.setMyCommands(
          commands.map((command) => ({ command, description: text[command] })),
          {
            ...(scope ? { scope } : {}),
            ...(language ? { language_code: language } : {}),
          },
          signal as unknown as Parameters<
            typeof ctx.channel.bot.api.setMyCommands
          >[2],
        );
      }
  } catch {
    if (!ctx.signal.aborted) ctx.log("telegram.menu-failed");
  }
}
