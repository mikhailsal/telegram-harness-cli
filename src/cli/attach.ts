import { join } from "node:path";
import { Store } from "../store/index.js";
import type { Config } from "../core/config.js";
import type { Arguments } from "./arguments.js";
import { allowedFlags } from "./arguments.js";
import type { CliDependencies } from "./context.js";
import { CliError } from "./errors.js";

async function waitForTurn(
  store: Store,
  adapter: Awaited<ReturnType<NonNullable<CliDependencies["openAdapter"]>>>,
  args: Arguments,
  timeout: number,
): Promise<void> {
  const deadline = Date.now() + timeout * 1000;
  let active = store.listTurns(["submitting", "active", "uncertain"]);
  if (args.flags.has("now") && active.length) {
    const turn = active[0];
    if (!turn?.externalTurnId)
      throw new CliError(
        "E_DEPENDENCY",
        "Cannot identify the active turn for interruption",
        "Wait for the turn or use thc status",
        4,
      );
    await adapter.interrupt(turn.externalTurnId);
  }
  while (active.length) {
    if (Date.now() >= deadline)
      throw new CliError(
        "E_DEPENDENCY",
        "The active turn did not finish before the attach timeout",
        "thc status",
        4,
      );
    await new Promise<void>((resolve) => setTimeout(resolve, 250));
    active = store.listTurns(["submitting", "active", "uncertain"]);
  }
}

export async function attach(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
  config: Config,
): Promise<void> {
  allowedFlags(args, ["now"]);
  const path = join(directory, ".thc", "store.db");
  const store = new Store(path, true);
  try {
    const factory =
      deps.openAdapter ?? (await import("../core/adapter.js")).openAdapter;
    const adapter = await factory(directory, config, store);
    try {
      await adapter.reconcile(store.getCursor("adapter-cursor"));
      await waitForTurn(store, adapter, args, config.attach_wait_timeout);
      const target = await adapter.attach();
      await deps.exec(target.command, target.args, {
        cwd: directory,
        env: { ...deps.env, ...target.env },
        inherit: true,
      });
    } finally {
      await adapter.close();
    }
  } finally {
    store.close();
  }
}
