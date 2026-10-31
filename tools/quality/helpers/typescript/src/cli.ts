// Command dispatch and exit statuses: 0 with a response on stdout, 2 for a
// request the helper cannot serve, 1 for anything unexpected.

import { readFile } from "node:fs/promises";
import { runEslintConfig } from "./eslint-config.ts";
import {
  describe,
  parseRequest,
  PROTOCOL,
  RequestError,
  type JsonObject,
} from "./protocol.ts";
import { runImports, runStructure } from "./structure.ts";
import { runTypes } from "./typecheck.ts";

export interface Io {
  readonly write: (text: string) => void;
  readonly warn: (text: string) => void;
}

type Command = (request: JsonObject) => object | Promise<object>;

const COMMANDS = new Map<string, Command>([
  ["structure", runStructure],
  ["imports", runImports],
  ["types", runTypes],
  ["eslint-config", runEslintConfig],
]);
const USAGE = `usage: main.ts {${[...COMMANDS.keys()].join(",")}} <request.json>`;

function parseArguments(argv: readonly string[]): readonly [Command, string] {
  const [name, path, ...rest] = argv;
  if (name === undefined || path === undefined || rest.length > 0) {
    throw new RequestError(USAGE);
  }
  const command = COMMANDS.get(name);
  if (command === undefined) {
    throw new RequestError(`unknown command "${name}"; ${USAGE}`);
  }
  return [command, path];
}

async function readRequest(path: string): Promise<JsonObject> {
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    throw new RequestError(`cannot read the request: ${describe(error)}`);
  }
  return parseRequest(text);
}

function oneLine(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** Run one command; return the exit status. */
export async function run(argv: readonly string[], io: Io): Promise<number> {
  try {
    const [command, path] = parseArguments(argv);
    const response = await command(await readRequest(path));
    io.write(`${JSON.stringify({ protocol: PROTOCOL, ...response })}\n`);
    return 0;
  } catch (error) {
    io.warn(`quality-typescript-helper: ${oneLine(describe(error))}\n`);
    return error instanceof RequestError ? 2 : 1;
  }
}
