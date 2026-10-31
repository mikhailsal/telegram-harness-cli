# telegram-harness-cli

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. Route only to skills in the session's available-skills list; answer directly for quick questions or small scoped edits.

Key routing rules:

- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate, unless one read-only check confirms the cause and a narrow fix (then fix it and run the focused test)
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec

## Quality

The repository uses the ai-boilerplate quality gate. After staging changes, run
`./quality bootstrap --pre-push` for setup, `./quality doctor` to verify the
installation, and `./quality check --all` for the staged candidate. Use
`./quality test --all` for tests, `./quality fix` for safe working-tree fixes,
and `./quality audit` for dependency advisories. CI runs the gate and audit;
the managed pre-commit and pre-push hooks must not be bypassed.

TypeScript uses strict compiler checks, type-aware ESLint, Prettier, and Vitest
with V8 coverage. Shell scripts are checked with ShellCheck. The policy in
`quality.toml` limits source files to 400 lines and functions to 50 code lines;
it also defines the other complexity and coverage thresholds. Register new
components and their tests in that policy before implementing them.

For local ShellCheck coverage, install the companion hook after bootstrap:
`printf '#!/bin/sh\nset -eu\nexec python3 tools/quality/launcher/scripts/shellcheck-revision.py --pre-push\n' > .git/hooks/pre-push.local && chmod +x .git/hooks/pre-push.local`.
The CI gate checks tracked `.sh` files in the tested revision with the same
script; install ShellCheck locally before pushing shell changes.
