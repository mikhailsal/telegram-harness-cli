# TODOS

## Distribution

### Install the latest release instead of main

**What:** `install.sh` installs the newest GitHub release tag instead of the newest commit on main.

**Why:** today `install.sh` reads `commits/main` (`install.sh:68`) and installs that commit, so a version tag and its GitHub release change nothing for users. Every merge reaches a new install at once, before the live acceptance and the Telegram checklist that a release requires.

**Pros:** users get only code that passed release acceptance; the version a user runs matches a CHANGELOG entry. **Cons:** a fix waits for the next release; `thc init --update` and `.thc/version` have to follow tags too; one more moving part when no release exists yet.

**Context:** raised after 0.1.2, when `docs/testing.md` added the step that creates a GitHub release from the CHANGELOG. Kept out of that change because it alters how every user receives updates.

**Effort:** human S / CC S
**Priority:** P2
**Depends on:** a GitHub release for every version tag (the `gh release create` step in `docs/testing.md`)

## Testing

### The live self-check test times out on a loaded machine

**What:** `tests/core/release-acceptance.test.ts` "checks live scenarios without modifying the isolated artifact" fails with `Test timed out in 60000ms` when the machine is busy.

**Why:** the test copies `node_modules`, fingerprints the copy several times and runs a nested Vitest run, so its time grows with CPU load. Measured on 8 cores: 24 s idle, 53 s with 24 busy loops, and a timeout at 64 s with 40 busy loops. It failed once in `./quality check --all` during the /ship of `feat/settings-fast-status-doctor`, at a tree whose commit hook had passed it; that branch does not touch the test, `scripts/test-live.ts` or `tests/live/`.

**Pros:** a red gate on a busy machine stops being noise. **Cons:** a longer timeout raises a test limit, which needs the owner's agreement; a cheaper test would have to keep the tamper check that the fingerprints provide.

**Context:** the nested run already has its own 30 s cap (`spawnSync` in `scripts/test-live.ts`), so the 60 s test timeout mostly bounds the copying and hashing. Options: raise the timeout with these measurements, or fingerprint the copy fewer times. The test needs no Codex login or network and already runs in the CI `gate` job, where it passes; it has failed only in local hooks on a busy machine. Moving it to CI only would break the rule that the hooks and CI run the same checks.

**Effort:** human S / CC S
**Priority:** P1
**Depends on:** the owner's decision on raising the timeout

**Completed:** unreleased (2026-11-22). The test copies only the installed packages the self-check loads (the runtime dependencies and Vitest with their own, read from `node_modules/.package-lock.json`) and none of this checkout's run caches. It no longer repeats the two fingerprints `checkedScenario` already makes, and the owner agreed to a 90 s timeout. On 8 cores it took 13.7 s idle and timed out at 97 s with 48 busy loops; now it takes 6.8 s idle, 39 s with 48 busy loops and 55 s with 64.

## Settings commands

### The model-rejected hold: `/effort` releases it and the recover button goes stale

**What:** after Codex rejects the Telegram model, queued messages stay held until the model actually changes, and the notice's `Switch to <default>` button keeps working until then.

**Why:** two paths break the hold today. `/effort` on a rejected model that `model/list` still lists commits the new effort with the same model (`effortCommand` in `src/core/harness-settings-command.ts`), which releases the hold, so the next queued message goes to the rejected model and fails again; this predates `/fast`. And `/fast` during the hold keeps the queue held but changes the desired-settings version, so the notice's button answers "Already resolved" (the `recover` handler in `src/core/harness-settings-cards.ts`) while the queue still waits; the owner has to type `/model`.

**Context:** review notes of #31 (Codex adversarial and structured passes). `docs/cli.md` says a `/model` or `/effort` choice releases the hold; the fix should release it on `/effort` only when the model differs from the rejected one, and compare the button's version against the model alone. Start with a test that holds the queue through `modelNotFound`, then sends `/effort` and `/fast`.

**Effort:** human S / CC S
**Priority:** P2
**Depends on:** None

### `/fast` edge cases

**What:** four small `/fast` behaviors found in the review of #31.

**Why:**

- `/fast on` falls back to the model's first tier when none is named Fast (`fastTier` in `src/core/harness-settings-tier.ts`), so a model that lists only a slower tier gets that one; the confirmation names it, but "fast" is then wrong.
- A bare `/fast` while fast mode is on still reads `model/list` (`runSettingsCommand` in `src/core/harness-settings-command.ts` treats only `/fast off` as listless), so a catalog failure keeps fast mode on although turning it off only writes `null`.
- `/model reset` after `/fast off` replies "Telegram override removed" but keeps the explicit standard-tier override (`resetModel`, same file); with fast mode on it cannot say whether the running model lists the tier.
- The live status shows nothing for a turn sent with the explicit standard tier (`src/core/status-settings.ts`), so it looks like a turn sent without a tier override.

**Context:** only the first sends Codex a request the owner did not mean, and only for a model whose tiers include none named Fast. Fix them together, each with a test in `tests/core/harness-settings-*.test.ts`.

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None

## Engine

### A store error before submit leaves the turn submitting

**What:** a failure to write the adapter descriptor, the execution record or the sent-settings cursor turns the Telegram turn uncertain at once, like a failed submit.

**Why:** `submitTurn` in `src/core/engine-submit.ts` calls `ctx.persistAdapter()`, `recordExecution` with the sent-settings cursor and the first `statuses.refresh()` after moving the turn to `submitting` but before the `try` around `adapter.submit`. A store error there leaves the turn `submitting`, and the queue waits until the next reconnect or restart turns it uncertain.

**Context:** review note of #31 (Codex structured pass). The writes moved before the first status frame so the status can name the settings sent. Move these calls inside the `try`, keeping their order before `adapter.submit`, and add tests with a store that throws on the `adapter-descriptor` cursor and on the sent-settings cursor. `persistAdapter` was found by the Codex review of #32.

**Effort:** human S / CC S
**Priority:** P2
**Depends on:** None

## Doctor

### `thc doctor` can wait 30 s for a daemon that stops answering

**What:** the `model/list` check closes its socket at once when it ends.

**Why:** `src/cli/doctor-codex.ts` removes the abort listener in `finally` and then calls the graceful `transport.close()`. A daemon that answers `model/list` and then ignores the close frame keeps `thc doctor` alive up to 30 s more, past its 5 s budget.

**Context:** review note of #31 (Codex structured and adversarial passes). Call `transport.terminate()` in the `finally`; the hung-daemon test in `tests/cli/doctor-codex.test.ts` can cover the answered-then-silent case.

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None

## Completed

### Show model and effort in the live status draft

**What:** add the active model/effort to the per-turn status message.

**Why:** confirms at a glance which model is answering.

**Pros:** visible proof a switch took effect. **Cons:** touches `src/core/status.ts`, a hot file.

**Context:** deferred from the model/effort plan (row E6).

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None (the model/effort commands shipped in 0.1.0)

**Completed:** unreleased (2026-11-21). The first status line of a Telegram turn names the model, effort and fast tier from its `turn-execution/<turn>/submit` record (`src/core/status-settings.ts`), which submitTurn now writes before the first status frame. A turn sent without an override names none.

### Service tier (`/fast`) toggle

**What:** a `/fast` command that sets `serviceTier` on `turn/start`.

**Why:** Codex exposes service tiers per model (`model/list` `serviceTiers`); owners who switch models may also want speed tiers.

**Pros:** reuses the `/model` settings path. **Cons:** another knob; tier ids vary by account.

**Context:** deferred from the model/effort plan (`docs/designs/telegram-model-effort.md`, row E4). Start from `harness-settings/desired`.

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None (the model/effort commands shipped in 0.1.0)

**Completed:** unreleased (2026-11-21). `/fast` toggles and `/fast on|off` sets the tier (`src/core/harness-settings-tier.ts`). On sends the current model's tier that Codex names Fast, off sends `serviceTier: null`, which a live probe of codex-cli 0.169.0 showed returns the thread to the standard tier even over a `service_tier` in its config. The tier is stored in `harness-settings/desired` and model changes keep it.

### thc doctor: report whether model switching is supported

**What:** `thc doctor` checks that the installed Codex answers `model/list` (codex-cli 0.169.0 or newer) and says so next to the tested-baseline line.

**Why:** an owner who gets `settingsUnsupported` runs `thc doctor`, as the reply tells them to. Doctor only compares the installed Codex version with the tested baseline (`src/cli/admin.ts:137`), so it shows a version mismatch and no word about model switching.

**Pros:** the error's fix line leads to a real answer. **Cons:** one more RPC in doctor; it needs the app-server running.

**Context:** DX review of the model/effort plan (`docs/designs/telegram-model-effort.md`, DX-7). When this item was written the baseline was 0.166.0 and doctor reported "matches" on a Codex without `model/list`; 0.1.1 moved the baseline to 0.169.0, which removed that false "matches".

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None (the model/effort commands shipped in 0.1.0)

**Completed:** unreleased (2026-11-21). `thc doctor` reports `version.modelList` (`src/cli/doctor-codex.ts`): `supported`, `unsupported` for a Codex without `model/list`, or `unavailable` when the Codex daemon is not running. It never starts the daemon.

### A second `thc stop` kills the bridge mid-shutdown

**What:** a `thc stop` run while the bridge is already shutting down sends it another SIGTERM. The bridge handles SIGTERM with `process.once` (`src/core/runtime.ts`), so the second one ends it at once, without closing the adapter, the engine or the store.

**Why:** found by /qa of #20 (low severity). It reproduces on real systemd. The folder's unit runs a bridge stand-in that exits 3 s after SIGTERM, `thc stop` waits, and a second `thc stop` from another shell logs `SIGTERM #2` 0.5 s later. The second stop sees `ActiveState=deactivating`, so `requestServiceStop` (`src/cli/service-unit.ts`) returns false and `stop()` falls back to `stopDirectory`. The same double signal happened before #20. A single `thc stop` can send it too when `systemctl stop --no-block` times out (5 s) after systemd queued the job, which needs a stalled user bus: `requestServiceStop` then returns false and `stop()` signals as well (found by /ship's adversarial review of #20).

**Pros:** a stop that systemd already runs could be waited for instead of signalled again; keeping the SIGTERM handler for the whole shutdown would also cover other tools. **Cons:** touches the signal path; #20 left `process.once` out of scope.

**Context:** /qa report `.gstack/qa-reports/run-20261120T080352Z/` capture 014. The store is crash-safe, so no data loss was seen; the risk is an unclean shutdown.

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None

**Completed:** v0.1.3 (2026-11-21). The bridge keeps its SIGTERM handler until shutdown ends (`src/core/runtime.ts`), so a repeated SIGTERM no longer cuts the shutdown short.
