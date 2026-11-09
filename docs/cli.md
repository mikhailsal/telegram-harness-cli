# CLI guide

## First start

Run the single command in the [README quickstart](../README.md#getting-started)
from the intended project. It downloads the installer completely before running
it, resolves a fixed source commit, and builds with the locked dependencies.
The build installs its development dependencies (TypeScript) even when
`NODE_ENV=production` or npm's `omit=dev` asks to skip them.
A completed source build is reused on subsequent runs of that commit. The
bootstrap requires network access even when a build is cached.

Compatible Node/npm and an existing Codex executable are reused. Missing
Node 24.22.0 and Codex 0.166.0 are installed in persistent per-user toolchain
directories; existing Codex installations are not upgraded. Interactive setup
starts native Codex login if authentication is needed.

Plain `init` prompts for a bot token without echoing it, verifies its identity
with Telegram, and stores it outside the workspace with mode 0600. The bot's
numeric ID is discovered automatically. `init --token-file /secure/bot-token`
uses an existing credential instead. During the user-ID prompt, send a private
message to the bot and enter the numeric ID it returns. This terminal entry
explicitly authorizes the user; the first unknown sender is not automatically
trusted.

Each project needs a different bot. Initialization checks persistent bot
bindings and registered projects for this Linux user before polling for an ID.
A project whose bridge is still running keeps its bot, even if its
configuration file was removed; for a project registered before bot bindings
existed, the bot is read from the identity its bridge stored, and setup refuses
when that cannot be read. Another process or another machine can still use the same token; do not share
it between pollers. The product has no fixed total project/bot count; practical
concurrent capacity has not been load-tested.

Rerunning plain `init` keeps the existing configuration and selected version.
An active service or foreground bridge is left running; a stopped installation
is resumed, and a resumed installation that has not delivered an answer yet
waits for its first answer again. When the folder keeps a version whose
database schema differs from the installer's, setup leaves the database
untouched and skips that wait, so the kept version can still open it.
Use `--update` or `--to VERSION` explicitly
to change versions. An explicit `--sandbox`, `--user-id` or `--token-file` that
differs from the saved configuration is rejected with `E_CONFIG` instead of
being ignored; edit `.thc/config.json` to change settings. A running bridge
reads its configuration only when it starts, so apply an edit with `thc stop`
followed by `thc init`; a plain rerun reports the bridge as running and does
not reload anything. `--dir PATH` selects the project folder and creates it
when it does not exist yet. Concurrent setup processes serialize shared
installation and registration work.

Sandbox choices are `read-only`, `workspace-write` and `danger-full-access`.
The choice is required. The default Codex protocol adapter starts its daemon
automatically when needed.

Without a terminal, supply all required values explicitly:

```sh
node /path/to/telegram-harness-cli/dist/cli/main.js init \
  --token-file /secure/bot-token \
  --user-id 123 \
  --sandbox workspace-write
```

By default, `init` enables and starts a systemd user service and waits up to
120 seconds for a new message to receive its first answer. The unit pins the
Node executable and records the setup invocation's PATH, with `~/.local/bin`
added at the end when it is missing so the agent can run the managed `thc`;
other shell environment variables are not copied into the service. Configure any custom
Codex environment, such as a non-default `CODEX_HOME`, in the user service's
environment before starting it. The token file must be a regular file with
mode 0600.

The managed CLI is installed as `~/.local/bin/thc`. Add that directory to
your shell's PATH, or use the absolute executable path. The executable keeps
the pinned Node and any installer-provided Codex reachable, so it also works
from a shell that has neither. A Node that the bootstrap installed privately
comes first on PATH, because Codex starts through `env node`; a system Node
never reorders your PATH, and Codex is only appended. Configuration,
database and downloaded files are stored in the project's ignored `.thc/`.
Initialization adds managed history instructions to `AGENTS.md` and installs
the history skill under `.agents/skills/thc/`.

If `init` warns about linger, `loginctl enable-linger "$USER"` allows the
user service to survive logout. This is a host setting; follow your host's
authorization policy if it requires administrator approval.

### Foreground use and preparation only

`--no-start` writes the installation and unit file without enabling or starting
the service. It still checks that Codex is installed and signed in. This also
allows foreground use on Linux without a running systemd user session:

```sh
node /path/to/telegram-harness-cli/dist/cli/main.js init \
  --token-file /secure/bot-token --user-id 123 \
  --sandbox workspace-write --no-start
thc start
```

`thc start` runs in the foreground and keeps that terminal occupied. Use a
second terminal for inspection, and Ctrl+C to end the foreground process.
To enable the prepared background service instead, run `thc init`
from the configured project in a working systemd user session.

### Choosing tmux

For tmux, initialize with `--no-start`, install tmux, then edit the existing
`.thc/config.json` and change `codex.adapter` from `"protocol"` to `"tmux"`.
Run `thc start` in the foreground, or `thc init` to start the user
service. There is no `--adapter` initialization flag. The measured baseline
for both adapters is documented in the [README](../README.md).

### Troubleshooting

| Symptom                                   | Check or next action                                                                                                                                                          |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `thc` is not found                        | Add `~/.local/bin` to PATH or use `~/.local/bin/thc`.                                                                                                                         |
| No answer after initialization            | Run `thc status`, `thc doctor`, then `thc tail --lines 50` in the configured project. Send a new private message after setup starts the bridge.                               |
| Codex cannot be found or is not signed in | Check `codex --version` and `codex login status` as the service's Linux user. After moving Node or Codex, rerun initialization with `--update` from the intended environment. |
| Cannot connect to the systemd user bus    | Use a working systemd user session for background setup, or initialize with `--no-start` and use foreground `thc start`.                                                      |
| Telegram reports a polling conflict       | Stop the other program using this bot token; one bridge should poll it.                                                                                                       |

## Daily use

Use `thc status`, `thc doctor`, and `thc tail` to inspect the bridge. `thc stop`
signals only the process that owns the folder lock and waits up to 15 seconds
for it to exit, so `thc stop && thc init` is safe; if the bridge is still
running after that, it fails with `E_ALREADY_RUNNING`. `thc attach` waits for the
active turn; `thc attach --now` requests an interrupt when the turn is known.

In Telegram, `/mode queue|steer` controls follow-ups and `/format rich|regular`
selects the next turn's answer format. The mode chooser, confirmations and
Telegram status use the same names: Queue and Steer (send immediately), with
localized explanations. Without an argument, either command opens
a chooser. Direct arguments and button selections produce the same temporary
confirmation; the next request clears it. Answer format does not change the
animated service status. `/new` and completed standalone compaction also produce
temporary service confirmations; user command messages remain in history.
Regular answers finish a rich preview and then convert that same message to
ordinary HTML formatting. The bridge checkpoints its ID before conversion;
recovery retries the edit. Cancellation and preview relocation still finalize
and delete a temporary status because action cancellation did not clear rich
previews in the tested Telegram client.

`thc tail` reads `.thc/bridge.log`. `setting.applied` records applied mode and
format changes, and `turn.execution` records the effective settings before a
submission or steering attempt. Their durable audit entries are also stored in
`.thc/store.db` under `meta` keys `setting-applied/UPDATE_ID` and
`turn-execution/TURN_ID/submit|steer`, so log rotation does not erase them.
`telegram.api` records API methods, IDs, timing and outcomes for status and
delivery diagnostics without recording message text or credentials. A successful
API response alone does not prove that a Telegram client removed its preview.

Use `thc search "words"`, `thc around "2026-11-07 18:00:00"`, and
`thc day 2026-11-07` to read visible conversation history. `search --any`
accepts any term; `--fts` accepts raw FTS5 syntax. `--role user|agent`,
`--since`, `--max-chars`, and `--after` narrow or continue results. The
commands do not expose internal message IDs in their output. `thc render
--turn TURN_ID` shows stored visible messages without restarting work. Use
`thc render response.md` or pipe Markdown to `thc render` to apply the same
visibility rules as outgoing Telegram messages without a configured folder.

`--since` accepts a date, a displayed timestamp, or relative days/hours such as
`7d` and `12h`. Invalid calendar dates and out-of-range times are rejected.

`thc init --update` selects the installed current package for this folder.
`thc init --to VERSION --no-start` selects an installed version only if it can
read the current database schema. The installer creates a database backup
before changing versions and never automatically restores an older database.
Each folder records its version separately. The managed `~/.local/bin/thc`
shim chooses that folder's version; outside a folder it chooses the newest
installed version.
The versioned installation uses the package's locked production dependencies;
it requires npm during initialization and does not install packages on startup.

Errors follow the contract in [errors](errors.md). Each command's `--help`
shows two examples.

## Development from source

The package is not published to npm. For development rather than product setup:

```sh
git clone https://github.com/mikhailsal/telegram-harness-cli.git
cd telegram-harness-cli
npm ci
npm run build
```

Use Node.js 24.22.0 or newer with npm. Invoke the built CLI with its absolute
path from a separate workspace. The README installer is the product setup path;
it does not clone source into the agent's project or alter shell startup files.
