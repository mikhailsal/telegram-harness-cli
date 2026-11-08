# Codex originator live smoke

On 2026-10-31, one real-provider turn passed with Codex CLI 0.166.0 and the existing configured login and default model, displayed as GPT-6.1-Sol. In a dedicated tmux server, the CLI ran with `--no-daemon -s read-only -a never` and a unique synthetic `CODEX_INTERNAL_ORIGINATOR_OVERRIDE` value beginning with `thc-`. The prompt was `Return THC_LIVE_OK exactly. Do not use tools.` The visible answer was exactly `THC_LIVE_OK`. There was no trust or approval prompt. The dedicated server was stopped afterward.

This is evidence that one real-provider turn accepted a unique originator override. The smoke value was not UUID-shaped like the adapter's generated marker, and the check did not exercise the complete bridge. No credentials, personal history, or provider request headers were copied into fixtures. The separate [tmux input spike](codex-tmux-input.md) documents fixture-provider tests and the conservative retry decision.
