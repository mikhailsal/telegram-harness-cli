# Testing and release acceptance

Automated tests use synthetic users and a local fake Bot API. Run the full suite
with `npm test`. The process-boundary tests can be run independently with
`npx vitest run tests/crash/runtime.test.ts`; they build the bridge, use a real
SQLite database and kill owned child processes at the inbox, submit and outbox
boundaries. The fake harness is held outside the child so a restart cannot erase
its record of a submission. These tests do not contact Telegram or a model.

For local development, `npm run test:live -- --allow-dirty` permits staged,
unstaged and untracked work. Its record is marked dirty and cannot authorize a
release. It records a SHA-256 fingerprint of the source worktree and fails if
the content or HEAD changes during the run. Before a release, run
`npm run test:live` from a clean checkout. It requires
Node 24, npm, access to the locked npm packages, an authenticated `codex`
command, a running Codex daemon and `tmux`. It copies the Git-enumerated build
inputs into a temporary directory, installs the locked dependencies with
`npm ci`, and builds there. It creates a separate temporary workspace and a new session
for each adapter, and sends a small synthetic prompt through a localhost fake
Bot API to each real adapter. It uses the normal configured Codex model and
login; it never sends a request to Telegram. The command fails if prerequisites
or either acceptance run fail. It writes `.quality/test-live.json` with the
result, timestamp, commit, tree, worktree fingerprint, package version, Codex
version and adapter results. Schema 2 also records the Node and npm versions and
a digest of the built package files and installed dependencies. The source and
artifact digests are checked again after the runs. Failed or partial records cannot authorize a release. The temporary
workspace is removed after each run; the new Codex sessions follow the normal
Codex retention policy.

Before tagging, use a separate test bot in a Telegram client for this manual checklist:

- Verify rich text, long answers, code blocks and a large code attachment.
- Verify the draft appears, its stop button works, and it restarts after a card.
  Check its native thinking animation while the task runs.
  It must disappear immediately with the final answer, without a lingering timer.
- Run a delayed task and send `/stop`: the preview must disappear without an AI
  answer or a lasting acknowledgement. Repeat with the native stop button.
  Check `/stop` in the command menu, including a client language other than
  English or Russian.
- Queue two messages: each answer must replace its preview without a redundant
  reply quote when it appears immediately below its question. Preserve the
  original message times; keep quotes when an answer needs a source reference.
- Tap both approval choices, check the collapsed details and stale-button toast.
- Check a question, a reaction, a reply and a forwarded message.
- Send a photo, album, voice note and document; verify captions and local paths.
- Check queue status, silent continuations and removal of temporary messages.
- Check that command inputs remain in the chat and visible history after another
  message and after ten minutes. `/new` and `/compact` should not add synthetic
  success acknowledgements; compaction shows its own progress status.
- Use `/format` to switch between Rich and Regular for the next turn. Rich
  supports formatted blocks and tables, but Telegram does not forward partial
  text quotes. Regular uses basic formatting, carries partial quotes, renders
  tables as text, and splits long replies at 4096 characters. The `format` field
  in `.thc/config.json` defaults to `"rich"` and also accepts `"regular"`.
- Restart the bridge during a turn and check the resulting answer and history.

Run `node scripts/release.ts` only after acceptance and the manual check. It
requires a clean tree and a passing clean-run record for the exact commit, tree,
worktree fingerprint and version from the preceding 24 hours. It repeats the
isolated locked build and compares the resulting artifact and toolchain with
the record; the tag verifier does the same, so it needs the same Node and npm
versions and access to the locked packages. It creates an annotated local version tag
containing the record and does not push it. The tag workflow checks that the
annotation matches the tagged commit and was fresh at tag creation. The record
is an operator attestation, not a cryptographic proof that a model or client
check was performed. The package is currently private; this workflow does not
publish it to npm.
