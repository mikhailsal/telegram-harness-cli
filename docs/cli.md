# CLI guide

## First start

Follow the [README quickstart](../README.md#getting-started) to build from source
and create a private token file. Run initialization in the folder where the
agent should work, using the absolute path to the built CLI. Plain `init`
prompts for the token file, Telegram user ID and sandbox in a terminal;
`init --token-file /secure/bot-token` skips only the first prompt.

During the user-ID prompt, send a private message to the bot and enter the
numeric ID it returns. Use a dedicated bot for this bridge: another process
polling the same bot can consume its updates or cause a polling conflict.

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
Node executable and records the setup invocation's PATH; other shell
environment variables are not copied into the service. Configure any custom
Codex environment, such as a non-default `CODEX_HOME`, in the user service's
environment before starting it. The token file must be a regular file with
mode 0600.

The managed CLI is installed as `~/.local/bin/thc`. Add that directory to
your shell's PATH, or use the absolute executable path. Configuration,
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
To enable the prepared background service instead, run `thc init --update`
from the configured project in a working systemd user session.

### Choosing tmux

For tmux, initialize with `--no-start`, install tmux, then edit the existing
`.thc/config.json` and change `codex.adapter` from `"protocol"` to `"tmux"`.
Run `thc start` in the foreground, or `thc init --update` to start the user
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
signals only the process that owns the folder lock. `thc attach` waits for the
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
