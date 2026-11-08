import { usage } from "./errors.js";
export interface Arguments {
  command: string;
  positionals: string[];
  flags: Map<string, string | boolean>;
}
const booleanFlags = new Set([
  "help",
  "json",
  "yes",
  "no-start",
  "update",
  "any",
  "fts",
  "full",
  "self-test",
  "force",
  "now",
]);
function parseOption(
  argv: string[],
  index: number,
  flags: Map<string, string | boolean>,
): number {
  const item = argv[index] ?? "";
  const [name, inline] = item.slice(2).split(/=(.*)/s, 2);
  if (!name) throw usage("Empty option");
  if (flags.has(name)) throw usage(`Option --${name} was specified twice`);
  if (booleanFlags.has(name)) {
    if (inline !== undefined) throw usage(`--${name} takes no value`);
    flags.set(name, true);
    return index;
  }
  const value = inline ?? argv[index + 1];
  if (!value || value.startsWith("--"))
    throw usage(`--${name} requires a value`);
  flags.set(name, value);
  return inline === undefined ? index + 1 : index;
}
export function parseArguments(argv: string[]): Arguments {
  const flags = new Map<string, string | boolean>();
  const positionals: string[] = [];
  for (let index = 0; index < argv.length; index++) {
    const item = argv[index];
    if (item?.startsWith("--")) index = parseOption(argv, index, flags);
    else if (item !== undefined) positionals.push(item);
  }
  return { command: positionals.shift() ?? "help", positionals, flags };
}
export function flag(args: Arguments, name: string): string | undefined {
  const value = args.flags.get(name);
  return typeof value === "string" ? value : undefined;
}
export function allowedFlags(args: Arguments, names: readonly string[]): void {
  for (const name of args.flags.keys())
    if (!["help", "json", "dir", ...names].includes(name))
      throw usage(`Unknown option --${name}`);
}
export function positive(
  value: string | undefined,
  fallback: number,
  max: number,
): number {
  if (value === undefined) return fallback;
  const number = Number(value);
  if (
    !/^\d+$/.test(value) ||
    !Number.isSafeInteger(number) ||
    number < 1 ||
    number > max
  )
    throw usage(`Expected a number from 1 to ${String(max)}`);
  return number;
}
