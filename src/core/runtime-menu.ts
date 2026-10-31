import { catalog } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";

export async function registerMenus(ctx: RuntimeContext): Promise<void> {
  try {
    for (const language of ["en", "ru"] as const) {
      const text = catalog[language];
      const commands = [
        "stop",
        "status",
        "new",
        "mode",
        "compact",
        "help",
      ] as const;
      const signal = AbortSignal.any([ctx.signal, AbortSignal.timeout(30000)]);
      await ctx.channel.bot.api.setMyCommands(
        commands.map((command) => ({ command, description: text[command] })),
        { language_code: language },
        signal as unknown as Parameters<
          typeof ctx.channel.bot.api.setMyCommands
        >[2],
      );
    }
  } catch {
    if (!ctx.signal.aborted) ctx.log("telegram.menu-failed");
  }
}
