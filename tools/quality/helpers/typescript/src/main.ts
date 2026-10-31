// Entry point: `node main.ts <command> <request.json>` prints one JSON response.

import { run } from "./cli.ts";

process.exitCode = await run(process.argv.slice(2), {
  write: (text) => process.stdout.write(text),
  warn: (text) => process.stderr.write(text),
});
