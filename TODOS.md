# TODOS

## Telegram

### Service tier (`/fast`) toggle

**What:** a `/fast` command that sets `serviceTier` on `turn/start`.

**Why:** Codex exposes service tiers per model (`model/list` `serviceTiers`); owners who switch models may also want speed tiers.

**Pros:** reuses the `/model` settings path. **Cons:** another knob; tier ids vary by account.

**Context:** deferred from the model/effort plan (`docs/designs/telegram-model-effort.md`, row E4). Start from `harness-settings/desired`.

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None (the model/effort commands shipped in 0.1.0)

### Show model and effort in the live status draft

**What:** add the active model/effort to the per-turn status message.

**Why:** confirms at a glance which model is answering.

**Pros:** visible proof a switch took effect. **Cons:** touches `src/core/status.ts`, a hot file.

**Context:** deferred from the model/effort plan (row E6).

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None (the model/effort commands shipped in 0.1.0)

## CLI

### thc doctor: report whether model switching is supported

**What:** `thc doctor` checks that the installed Codex answers `model/list` (codex-cli 0.169.0 or newer) and says so next to the tested-baseline line.

**Why:** an owner who gets `settingsUnsupported` runs `thc doctor`, as the reply tells them to. Doctor only compares the installed Codex version with the tested baseline (`src/cli/admin.ts:137`), so it shows a version mismatch and no word about model switching.

**Pros:** the error's fix line leads to a real answer. **Cons:** one more RPC in doctor; it needs the app-server running.

**Context:** DX review of the model/effort plan (`docs/designs/telegram-model-effort.md`, DX-7). When this item was written the baseline was 0.166.0 and doctor reported "matches" on a Codex without `model/list`; 0.1.1 moved the baseline to 0.169.0, which removed that false "matches".

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None (the model/effort commands shipped in 0.1.0)

### A second `thc stop` kills the bridge mid-shutdown

**What:** a `thc stop` run while the bridge is already shutting down sends it another SIGTERM. The bridge handles SIGTERM with `process.once` (`src/core/runtime.ts`), so the second one ends it at once, without closing the adapter, the engine or the store.

**Why:** found by /qa of #20 (low severity). Repro on real systemd: the folder's unit runs a bridge stand-in that exits 3 s after SIGTERM, `thc stop` waits, and a second `thc stop` from another shell logs `SIGTERM #2` 0.5 s later. The second stop sees `ActiveState=deactivating`, so `requestServiceStop` (`src/cli/service-unit.ts`) returns false and `stop()` falls back to `stopDirectory`. The same double signal happened before #20. A single `thc stop` can send it too when `systemctl stop --no-block` times out (5 s) after systemd queued the job, which needs a stalled user bus: `requestServiceStop` then returns false and `stop()` signals as well (found by /ship's adversarial review of #20).

**Pros:** a stop that systemd already runs could be waited for instead of signalled again; keeping the SIGTERM handler for the whole shutdown would also cover other tools. **Cons:** touches the signal path; #20 left `process.once` out of scope.

**Context:** /qa report `.gstack/qa-reports/run-20261120T080352Z/` capture 014. The store is crash-safe, so no data loss was seen; the risk is an unclean shutdown.

**Effort:** human S / CC S
**Priority:** P3
**Depends on:** None

## Distribution

### Install the latest release instead of main

**What:** `install.sh` installs the newest GitHub release tag instead of the newest commit on main.

**Why:** today `install.sh` reads `commits/main` (`install.sh:68`) and installs that commit, so a version tag and its GitHub release change nothing for users. Every merge reaches a new install at once, before the live acceptance and the Telegram checklist that a release requires.

**Pros:** users get only code that passed release acceptance; the version a user runs matches a CHANGELOG entry. **Cons:** a fix waits for the next release; `thc init --update` and `.thc/version` have to follow tags too; one more moving part when no release exists yet.

**Context:** raised after 0.1.2, when `docs/testing.md` added the step that creates a GitHub release from the CHANGELOG. Kept out of that change because it alters how every user receives updates.

**Effort:** human S / CC S
**Priority:** P2
**Depends on:** a GitHub release for every version tag (the `gh release create` step in `docs/testing.md`)

## Completed
