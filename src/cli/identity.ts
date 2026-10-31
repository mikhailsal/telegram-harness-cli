import { Bot } from "grammy";

export type IdentityApi = Pick<Bot["api"], "getUpdates" | "sendMessage">;

export async function respondWithPrivateIds(
  token: string,
  signal: AbortSignal,
  api: IdentityApi = new Bot(token).api,
): Promise<void> {
  let offset = 0;
  while (!signal.aborted) {
    const updates = await api.getUpdates(
      { offset, timeout: 1, allowed_updates: ["message"] },
      signal as unknown as Parameters<typeof api.getUpdates>[1],
    );
    for (const update of updates) {
      offset = Math.max(offset, update.update_id + 1);
      const message = update.message;
      if (message?.chat.type !== "private") continue;
      await api.sendMessage(
        message.chat.id,
        String(message.from.id),
        { disable_notification: true },
        signal as unknown as Parameters<typeof api.sendMessage>[3],
      );
    }
  }
}
