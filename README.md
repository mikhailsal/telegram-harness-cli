# telegram-harness-cli

`thc` connects a Telegram private chat to a long-lived console coding agent.
Codex is the first supported harness. The bridge owns its queue, delivered
history and recovery state; the agent answers normally.

Two Codex adapters are available: the shared app-server protocol and a
guarded tmux runner. Their measured compatibility baseline is Codex CLI
0.166.0. See the [protocol](docs/spikes/codex-two-clients.md) and
[tmux](docs/spikes/codex-tmux-input.md) experiments.

The tmux runner disables Codex's interactive startup update check for its
owned sessions. Manage Codex versions outside those sessions and use `thc doctor`
to compare the installed version with the tested baseline.

## Getting started

Open a terminal **inside the project where the agent should work**, then copy
this single command. Use the same command for your first installation, another
project, or a project that is already configured:

```sh
bash -c 'f=$(mktemp) && trap '\''rm -f "$f"'\'' EXIT && curl -fsSL https://raw.githubusercontent.com/mikhailsal/telegram-harness-cli/main/install.sh -o "$f" && bash "$f"'
```

Use Linux x64 or arm64 with Bash, curl, tar, sha256sum, flock, and a working
systemd user session. Run as the Linux user who will run the agent.

The installer prepares the CLI and any missing Node/npm and Codex dependencies
in your user directories. It keeps an existing Codex installation. If Codex
needs authentication, the wizard starts its normal sign-in flow.

The wizard then guides you through:

1. Create a bot with [@BotFather](https://t.me/BotFather), or use a dedicated
   bot you already own. Paste its **token** into the hidden terminal prompt.
   The wizard verifies the token, discovers the bot's ID and username, and
   stores the credential in a private file outside the project.
2. Send the bot a private message. It replies with your numeric Telegram user
   ID; enter that ID in the terminal to authorize yourself.
3. Choose the agent's access: `read-only`, `workspace-write` for project edits,
   or `danger-full-access`. There is no implicit choice.
4. Setup writes the project configuration and history instructions, starts its
   background service, and asks you to send a **new** message to the bot.
   It reports the first delivered answer, or diagnostics after 120 seconds.

The default adapter uses the shared Codex protocol and starts its daemon when
needed. A second run keeps the project's configuration and selected version;
a running bridge continues without a restart. A stopped bridge is started.
Explicit updates and preparation without starting a service are described in
the [CLI guide](docs/cli.md#first-start).

### One bot per project

Each project has its own bot, service, conversation, queue, and ignored `.thc/`
state directory. Run the same installation command in another project and
supply a different bot token. Setup rejects a bot already assigned to another
project for this Linux user. Use a dedicated bot: an unrelated program polling
it can consume updates or cause a conflict.

There is no fixed total bot limit in the application. Multiple project services
can run together; capacity depends on the machine and harness/provider limits.
No maximum simultaneous count has been load-tested.

Allowlisted Telegram users are trusted to run shell commands as this Linux
user. Groups are ignored; unallowlisted private users receive only their ID.
For diagnostics, use `~/.local/bin/thc status`, `~/.local/bin/thc doctor`, or
`~/.local/bin/thc tail --lines 50` inside the project. For foreground runs,
tmux selection, noninteractive setup, and source development, see the
[CLI guide](docs/cli.md).

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
