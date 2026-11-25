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
`npm run test:live` from a clean checkout. A clean run requires
the exact Node version in `.nvmrc` with its bundled npm 11.19.1, the toolchain
the tag workflow verifies with; on any other it stops after the build with an
error that names both. When `.nvmrc` moves to a Node release that bundles
another npm, update `RELEASE_NPM` in `scripts/release.ts` with it. It also requires access to the locked npm packages, an authenticated `codex`
command, a running Codex daemon and `tmux`. It copies the Git-enumerated build
inputs into a temporary directory, installs the locked dependencies with
`npm ci`, and builds there. It creates a separate temporary workspace and a new session
for each adapter, and sends a small synthetic prompt through a localhost fake
Bot API to each real adapter. It uses the normal Codex login and never sends a
request to Telegram. The run pins its reasoning effort to `low`, so that a slow
personal default (for example `xhigh`) cannot time out its steps, which wait 120
seconds each. It keeps the configured Codex model. `--effort <level>` and
`--model <id>` choose others: the tmux adapter starts Codex with them as `-c`
overrides, and the protocol adapter sends them with every turn. The command fails if prerequisites
or either acceptance run fail. It writes `.quality/test-live.json` with the
result, timestamp, commit, tree, worktree fingerprint, package version, Codex
version, the pinned model (`default` for the configured one) and effort, and
adapter results. Schema 2 also records the Node and npm versions and
a digest of the built package files and installed dependencies. The source and
artifact digests are checked again after the runs. Failed or partial records cannot authorize a release. The temporary
workspace is removed after each run; the new Codex sessions follow the normal
Codex retention policy.

Before tagging, the owner goes through this manual checklist with a separate
test bot in a Telegram client, or decides to release without it this time (see
[Releasing and versioning](releasing.md)):

- Verify rich text, long answers, code blocks and a large code attachment.
- Verify the draft appears, its stop button works, and it restarts after a card.
  Check its native thinking animation while the task runs.
  It must disappear immediately with the final answer, without a lingering timer
  or stop button. Observe the reply for another 35 seconds: its message must not
  disappear and reappear.
- Run a delayed task and send `/stop`: the preview must disappear without an AI
  answer or a lasting acknowledgement. Repeat with the native stop button.
  Check `/stop` in the command menu, including a client language other than
  English or Russian.
- Queue two messages: each answer must replace its preview without a redundant
  reply quote when it appears immediately below its question. Preserve the
  original message times; keep quotes when an answer needs a source reference.
- Tap both approval choices, check the collapsed details and stale-button toast.
- Check a question, a reaction, a reply and a forwarded message.
- Ask Codex to send a question with options through `request_user_input_async`
  during a task. Each question arrives as its own message with option buttons.
  In Queue mode a tap answers inside the running task, the buttons then show
  the answer, and a second tap shows the stale toast. A tap after the task
  ended starts the next task.
- Send a photo, album, voice note and document; verify captions and local paths.
- Check queue status, silent continuations and removal of temporary messages.
- Check that command inputs remain in the chat and visible history after another
  message and after ten minutes. `/new` confirms after the new session starts;
  standalone compaction confirms after completion. These service confirmations
  disappear on the next request or after ten minutes.
- Check `/mode queue` and `/format regular`, then their button choosers. Both
  paths retain the selected value in a temporary confirmation without buttons.
- Use `/format` to switch between Rich and Regular for the next turn. Rich
  supports formatted blocks and tables, but Telegram does not forward partial
  text quotes. Regular uses basic formatting, carries partial quotes, renders
  tables as text, and splits long replies at 4096 characters. The `format` field
  in `.thc/config.json` defaults to `"rich"` and also accepts `"regular"`.
  The animated service status remains the same in both formats. Check a
  multiline quote containing bold and italic spans: compatible spans belong to
  one quote, with no empty quote bars between lines.
- Restart the bridge during a turn and check the resulting answer and history.

The order of the release steps, who runs each one and how the version is
chosen are in [Releasing and versioning](releasing.md).

Run `node scripts/release.ts` only after acceptance and the manual check. It
requires a clean tree and a passing clean-run record for the exact commit, tree,
worktree fingerprint and version from the preceding 24 hours. It repeats the
isolated locked build and compares the resulting artifact and toolchain with
the record; the tag verifier does the same, so it needs the same Node and npm
versions and access to the locked packages. Both refuse to start on a toolchain
other than the one pinned by `.nvmrc`. It creates an annotated local version tag
containing the record and does not push it. The tag workflow checks that the
annotation matches the tagged commit and was fresh at tag creation. The record
is an operator attestation, not a cryptographic proof that a model or client
check was performed. The package is currently private; this workflow does not
publish it to npm.

The workflow has read-only repository access, so it does not create the GitHub
release. Once its check passes, create the release with the version's CHANGELOG
section as the notes, which `node scripts/release.ts --notes vX.Y.Z` prints from
the tagged commit. `gh release create --verify-tag` only checks that the tag
exists, so first confirm that the tag on GitHub still points at the commit the
workflow checked and that this check passed:

```sh
tag=vX.Y.Z
commit=$(git rev-parse "$tag^{commit}") &&
  test "$(git ls-remote origin "refs/tags/$tag^{}" | cut -f1)" = "$commit" &&
  test "$(gh run list --workflow release.yml --commit "$commit" \
    --json conclusion --jq '.[0].conclusion')" = success &&
  notes=$(node scripts/release.ts --notes "$tag") &&
  gh release create "$tag" --verify-tag --title "$tag" --notes "$notes"
```
