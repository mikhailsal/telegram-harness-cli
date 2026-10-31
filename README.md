# telegram-harness-cli

`thc` connects a Telegram private chat to a long-lived console coding agent.
Codex is the first supported harness. The bridge owns its queue, delivered
history and recovery state; the agent answers normally.

Two Codex adapters are available: the shared app-server protocol and a
guarded tmux runner. Their measured compatibility baseline is Codex CLI
0.166.0. See the [protocol](docs/spikes/codex-two-clients.md) and
[tmux](docs/spikes/codex-tmux-input.md) experiments.

## Getting started

Use Linux, Node.js 24.22.0 or newer, Git, Codex CLI and an existing Codex
login. The tmux adapter also needs tmux.

```sh
npm ci
npm run build
node dist/cli/main.js --help
```

To prepare a folder, place the bot token in a private file with mode 0600,
then run:

```sh
node /path/to/telegram-harness-cli/dist/cli/main.js init \
  --token-file /private/path/bot-token \
  --user-id YOUR_TELEGRAM_USER_ID \
  --sandbox workspace-write
```

`init` installs a pinned copy, writes the folder configuration and history
skill, and prepares a systemd user service. Use `--no-start` to prepare it
without starting the service. The sandbox choice is explicit. The
allowlisted Telegram users are trusted to run shell commands as this user.
Groups are ignored.

The [CLI guide](docs/cli.md) covers interactive setup, noninteractive flags,
update and rollback, errors, history search and attach.

## Behavior and recovery

- Incoming updates are committed before polling advances its offset.
- Work is queued by default. Supported steering targets an identified turn.
- A reconnect reconciles the harness before another submission. Tmux never
  automatically retypes an input whose outcome is unknown.
- Final delivery is retried from saved output. A crash after Telegram
  accepts a response can produce a duplicate; history records that risk.
- Chat history contains delivered text and visible user messages. Hidden
  agent markup, tool output and ephemeral status do not enter search.
- Photos, documents, voice messages and albums are downloaded as files;
  queued or active work protects its attachments from retention cleanup.
- Status, commands and decision cards have durable cleanup state.

State and downloaded files live in the folder's ignored `.thc/` directory.
Protect that directory and the bot token. A bot can retain unreceived
updates for at most 24 hours; another poller can consume them first.

## Validate changes

```sh
./quality bootstrap --pre-push
./quality doctor
./quality check --all
npm run test:coverage
```

The [quality policy](quality.toml) sets the source/function limits, strict
TypeScript checks and coverage floors. Stage the candidate before running
the quality gate. Git hooks run the same gate and must not be bypassed.
The [agent instructions](AGENTS.md) describe the full workflow.

Unit, integration and real-child crash tests use a localhost Bot API and
fake harness. Real-provider acceptance is separate:

```sh
npm run test:live -- --allow-dirty
```

That development mode cannot authorize a release. Clean release acceptance
binds the exact source, locked build, artifact and tool versions. See the
[testing guide](docs/testing.md) and
[release checklist](docs/release-checklist.md). A separate Telegram test
bot and the phone checklist are still required to verify Telegram's actual
rendering and buttons.

All project and imported tooling documentation is listed in the
[documentation index](docs/README.md).
