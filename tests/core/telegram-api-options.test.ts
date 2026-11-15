import { expect, it, vi } from "vitest";
import { botIdentity } from "../../src/cli/onboarding.js";
import { telegramApiOptions } from "../../src/core/config.js";
import { FakeServices } from "../fakes/servers.js";

it("points grammY at THC_TELEGRAM_API_ROOT when it is set", () => {
  expect(
    telegramApiOptions({ THC_TELEGRAM_API_ROOT: "http://127.0.0.1:9" }),
  ).toEqual({ apiRoot: "http://127.0.0.1:9" });
});

it("leaves the default Bot API when the variable is unset or empty", () => {
  expect(telegramApiOptions({})).toEqual({});
  expect(telegramApiOptions({ THC_TELEGRAM_API_ROOT: "" })).toEqual({});
});

it("asks the configured Bot API who the bot is during setup", async () => {
  const services = new FakeServices();
  await services.start();
  vi.stubEnv("THC_TELEGRAM_API_ROOT", services.url);
  try {
    expect(await botIdentity("123:synthetic")).toMatchObject({
      id: 123,
      username: "thc_fake_bot",
    });
    expect(services.calls.map((call) => call.method)).toEqual(["getMe"]);
  } finally {
    vi.unstubAllEnvs();
    await services.close();
  }
});
