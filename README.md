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

Use Linux, Node.js 24.22.0 or newer with npm, Git, and an authenticated
Codex CLI. The default background service needs a working systemd user
session. The optional tmux adapter also needs tmux. Run these commands as
the Linux user who will run the agent, without `sudo`.

Installation is currently from source; the package is not published to npm.
Keep the same terminal open through the following steps.

### 1. Download and build

```sh
git clone https://github.com/mikhailsal/telegram-harness-cli.git
cd telegram-harness-cli
npm ci
npm run build
THC_SOURCE_DIR="$PWD"
```

Check `codex --version` and `codex login status` before continuing. If needed,
run `codex login` for this Linux account.

### 2. Create a Telegram bot

Open [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot`, and
follow its prompts. Keep the bot's username so you can open its private chat.
Telegram's [bot setup guide](https://core.telegram.org/bots/features#botfather)
explains this step.

Create a private token file outside your project:

```sh
mkdir -p "$HOME/.config/thc"
(umask 077; touch "$HOME/.config/thc/bot-token")
chmod 600 "$HOME/.config/thc/bot-token"
```

Open that file in your text editor and save only the token supplied by
BotFather, on one line.

### 3. Initialize the agent's working folder

Initialize in the project where you want Codex to work. This can be an
existing project; the commands below create a separate example workspace.
The source checkout from step 1 and the agent's workspace serve different purposes.

```sh
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$HOME/projects/my-agent"
cd "$HOME/projects/my-agent"
node "$THC_SOURCE_DIR/dist/cli/main.js" init \
  --token-file "$HOME/.config/thc/bot-token"
```

The interactive setup will:

1. Ask for your Telegram user ID. While the terminal waits, send a private
   message to your new bot. It replies with your numeric ID; paste that
   number into the terminal.
2. Ask for a sandbox. Enter `workspace-write` for project edits or `read-only`
   for a read-only agent. There is no implicit sandbox choice.
3. Install a pinned copy, write the project configuration, history skill and
   managed `AGENTS.md` instructions, and enable the systemd user service. The service retains the PATH used
   during setup so it can find your Codex installation.
4. Print `Bridge started. Send a message to the bot.` Send a **new** message,
   such as `Reply: ready`. Setup reports `Working: first answer in ... s`
   after the first delivered answer, or diagnostic output after 120 seconds.

The default adapter is the shared Codex protocol. The bridge starts its
daemon when needed; no separate daemon command is required for this path.
Allowlisted Telegram users are trusted to run shell commands as this user.
Groups are ignored, and unallowlisted private users receive only their ID.

### 4. Check the installation

Run these from the agent's working folder:

```sh
thc status
thc doctor
thc tail --lines 50
```

`status` should show `running: true`. `doctor` reports the token, Codex login,
version compatibility, service persistence and history checks. Add
`$HOME/.local/bin` to your shell's PATH for future terminals; the installer
does not edit your shell startup files.

For a foreground run, setup without starting a service, tmux selection,
keeping the service alive after logout, and troubleshooting, see the
[first-start instructions in the CLI guide](docs/cli.md#first-start).

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
