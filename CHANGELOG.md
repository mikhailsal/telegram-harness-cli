# Changelog

All notable changes to `thc` are listed here. Versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.3] - 2026-11-21

### Fixed

- `thc stop` run through a symlinked folder path stops the bridge service that
  an older `thc init` set up under that path, also when the project's store
  does not record the folder. It used to signal the bridge directly, so systemd
  still reported the service as running, and the next `thc init` left the bot
  stopped.
- A second `thc stop` sent while the bridge is shutting down no longer ends it
  at once. The bridge now finishes closing Codex and its store first. Pressing
  Ctrl+C twice still ends a bridge started in the foreground at once.

## [0.1.2] - 2026-11-21

### Fixed

- `thc init` and `thc init --update` say when they wait for systemd to stop
  the bridge service. systemd can take up to 90 seconds for that, and setup
  printed nothing meanwhile, so it looked hung.
- `thc init` starts the service again when systemd is still stopping it and
  the bridge has not exited yet. It used to print "Already initialized and
  running" and leave the bridge stopped once the stop finished. A bridge
  started in the foreground with `thc start` is still left alone.
- `thc stop` stops a bridge that runs as the folder's systemd service through
  systemd. It used to signal the process directly, so systemd still reported
  the service as running while the bridge shut down. A `thc init` in that time
  printed "Already initialized and running", and the bot stayed stopped.
- When Codex restarts its daemon in the middle of a task and resumes the work
  while a newer Telegram message is still being sent, the resumed answer
  arrives as a reply to the original message. It used to be dropped, and the
  chat showed "🖥 ✓ Done, no text response" instead.

## [0.1.1] - 2026-11-17

### Fixed

- The tmux runner works with Codex 0.169.0. That version adds a hint line
  under the status line, and the runner took it for typed text, so every
  message failed with "Codex is not accepting input".
- The tmux runner also reads the composer when Codex shows a notice count
  ("1 warning · f2 to view") under the status line, for example near the
  weekly usage limit. Messages failed the same way.

### Changed

- The tested Codex version is now 0.169.0, the version `/model` needs and the
  live acceptance passes on. `thc doctor` compares against it, and the
  installer installs it when Codex is missing. An existing Codex is still not
  upgraded.
- `npm run test:live` pins its reasoning effort to `low` and takes
  `--effort <level>` and `--model <id>`, so release acceptance no longer
  depends on the Codex defaults of the machine it runs on.

## [0.1.0] - 2026-11-16

The first versioned release.

### Added

- A durable bridge between a Telegram private chat and a long-lived Codex
  session. The bridge owns the queue, the delivered history and the recovery
  state, so messages survive restarts.
- Two Codex adapters: the shared app-server protocol (the default) and a
  guarded tmux runner.
- One-command setup. The installer prepares Node, npm and Codex in your user
  directories. The `thc init` wizard then checks the bot token, authorizes
  your Telegram user, asks for the sandbox level and starts a systemd user
  service.
- One bot per project, each with its own service, conversation, queue and
  state directory.
- Telegram controls: `/mode queue|steer` for follow-ups, `/format rich|regular`
  for answers, `/stop`, `/new`, `/status` and button choosers.
- `/model` and `/effort` switch the Codex model and reasoning effort from
  Telegram:
  - A card lists the visible models with their descriptions. A tap saves the
    model, then offers its efforts.
  - `/model <id>`, `/model default` and `/model reset` change the model
    directly.
  - The choice applies to the next turn and to queued messages, and it stays
    until you change it.
- `/status` shows the model that runs now, what Telegram sets for the next
  turns, and why they differ.
- When Codex rejects a model, the bot says so once and offers a one-tap switch
  to Codex's default. Queued messages wait until you choose.
- Live status drafts with the thinking animation, rich final messages,
  approvals, attachments and history search.
- `thc doctor` compares the installed Codex with the tested baseline. Codex
  stays alive across `thc stop`, and daemon recovery is reported as it
  happened.

### Fixed

- Setup shows the user ID and sandbox prompts. It also writes a
  `WorkingDirectory` systemd accepts and keeps the caller's PATH for the
  service.
- History search rejects invalid dates and accepts relative hours.
- The tmux runner recognizes changing Codex activity headings and suppresses
  the interactive startup update prompt.
