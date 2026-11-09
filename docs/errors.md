# Error reference

Errors have a stable first line `thc: CODE: what` and final line `fix: command`.
`--json` writes an error object with the same code, fix, recovery, side effects,
and context. Exit codes are 2 for usage, 3 for configuration or environment,
4 for a dependency, and 5 for an unexpected internal failure.

## E_USAGE

An argument or option is invalid. Run `thc --help`.

## E_NEEDS_INPUT

A required init value is missing in non-interactive mode. Supply the flag named
in the error; see `thc init --help`.

## E_NO_STATE_DIR

No `.thc` directory was found. Change to the agent folder or run `thc init`.

## E_CONFIG

The configuration or token is invalid. Run `thc doctor` and fix the reported
configuration before restarting. `thc init` also reports it when `--sandbox`,
`--user-id` or `--token-file` contradicts the saved configuration; edit
`.thc/config.json` instead, then run `thc stop` and `thc init` so the running
bridge restarts with the edit.

## E_TIME_FORMAT

The time could not be parsed. Use a date or timestamp printed by `thc`.

## E_STORE_BUSY

The database was busy past its timeout. Retry and run `thc doctor` if it persists.

## E_STORE_SCHEMA_NEWER

The database schema is incompatible with this version. Use a compatible newer
version; never restore an older database automatically.

## E_DEPENDENCY

A required program, service, or store operation failed. Run `thc doctor`.

## E_ALREADY_RUNNING

Another process owns the folder lock. Use `thc status` or `thc attach`.
`thc stop` reports it when the bridge still runs 15 seconds after the stop
request.

## E_INTERNAL

An unexpected failure occurred. Send the redacted trace path reported by the
command to the operator.
