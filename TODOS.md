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

## Completed
