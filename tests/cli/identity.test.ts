import { expect, it } from "vitest";
import {
  respondWithPrivateIds,
  type IdentityApi,
} from "../../src/cli/identity.js";

it("replies with sender ID only for private messages and advances the update cursor", async () => {
  const controller = new AbortController();
  const sent: [number, string][] = [];
  const offsets: number[] = [];
  const api = {
    getUpdates: (request: { offset: number }) => {
      offsets.push(request.offset);
      if (offsets.length > 1) {
        controller.abort();
        return Promise.resolve([]);
      }
      return Promise.resolve([
        {
          update_id: 1,
          message: { chat: { type: "group", id: -9 }, from: { id: 5 } },
        },
        {
          update_id: 2,
          message: { chat: { type: "private", id: 7 }, from: { id: 7 } },
        },
      ]);
    },
    sendMessage: (chat: number, text: string) => {
      sent.push([chat, text]);
      return Promise.resolve({});
    },
  } as unknown as IdentityApi;
  await respondWithPrivateIds("unused", controller.signal, api);
  expect(sent).toEqual([[7, "7"]]);
  expect(offsets).toEqual([0, 3]);
});
