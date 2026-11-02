# CLI guide

Initialize in the agent folder with an existing private token file, an allowlisted
Telegram user ID, and an explicit sandbox mode:

```sh
thc init --token-file /secure/bot-token --user-id 123 --sandbox workspace-write --yes --no-start
thc start
```

The token file must be a regular file inaccessible to group and other users.
Use `thc status`, `thc doctor`, and `thc tail` to inspect the bridge. `thc stop`
signals only the process that owns the folder lock. `thc attach` waits for the
active turn; `thc attach --now` requests an interrupt when the turn is known.

In Telegram, `/mode queue|steer` controls follow-ups and `/format rich|regular`
selects the next turn's answer format. Without an argument, either command opens
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
