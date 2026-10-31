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
