export interface ErrorInfo {
  code: string;
  what: string;
  fix: string;
  recovery: string;
  side_effects: string;
  context: Record<string, string>;
}
export class CliError extends Error {
  readonly info: ErrorInfo;
  readonly exitCode: number;
  constructor(
    code: string,
    what: string,
    fix: string,
    exitCode: number,
    context: Record<string, string> = {},
  ) {
    super(what);
    this.name = "CliError";
    this.exitCode = exitCode;
    this.info = {
      code,
      what,
      fix,
      recovery: "Follow the fix command and retry.",
      side_effects: "none",
      context,
    };
  }
}
export const explanations: Record<string, string> = {
  E_USAGE: "The command or its arguments are invalid.",
  E_NEEDS_INPUT: "A required value was not supplied in non-interactive mode.",
  E_NO_STATE_DIR: "No .thc directory was found in this folder or its parents.",
  E_CONFIG: "The configuration or token is invalid.",
  E_TIME_FORMAT: "Use an ISO date or a timestamp printed by thc.",
  E_STORE_BUSY: "The database could not be read within its busy timeout.",
  E_STORE_SCHEMA_NEWER:
    "The database was written by an incompatible newer release.",
  E_DEPENDENCY: "A required external program or service failed.",
  E_ALREADY_RUNNING: "Another bridge holds the folder lock.",
  E_INTERNAL:
    "An unexpected internal error occurred; send the trace to the operator.",
};
export function usage(message: string): CliError {
  return new CliError("E_USAGE", message, "thc --help", 2);
}
