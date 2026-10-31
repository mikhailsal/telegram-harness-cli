# Codex tmux input spike

Run on 2026-10-31 with `codex-cli 0.166.0`, tmux 3.2a, a fresh `CODEX_HOME`, `codex --no-daemon -a never`, and a dedicated tmux server and workspace. The provider was a **local Responses API fixture**, not an OpenAI service or a real model. No real inference was performed. The fixture accepted requests at `127.0.0.1:18991/v1/responses`, saved the JSON body, delayed three seconds and returned `response.created`, `response.output_item.done`, and `response.completed` SSE events. Its event shapes follow `codex-rs/core/tests/common/responses.rs` in the local Codex source. All messages below were synthetic.

## Results

- A `tmux paste-buffer -p` of one 10,073-character synthetic bridge envelope, including multiple lines and 10,000 `x` characters, produced one matching rollout `response_item` user message and one matching user input in the fixture provider request. Both held the full 10,072 characters after Codex removed the final newline. There was no `[Pasted Content …]` wrapper or other framing in that user input. The separate environment-context user item is not part of the submitted message. The original and provider input are in [rollout fixtures](../../tests/fixtures/codex-tmux/rollout/); the original input is the recorded text plus one final LF. This establishes the result for this synthetic sample and fixture provider, not all possible payloads or providers.
- With bracketed paste, 1,000 ASCII characters appeared directly, while 1,001 appeared as `[Pasted Content 1001 chars]` in the composer. The full long text was transmitted on submission. The source at `codex-rs/tui/src/bottom_pane/chat_composer.rs` sets `LARGE_PASTE_CHAR_THRESHOLD` to 1000 and checks `char_count > ...`. The threshold is in characters, not bytes; only ASCII boundary cases were measured here.
- `tmux send-keys M-Enter` and `C-j` each inserted a newline between `alpha` and `beta`. `send-keys S-Enter` literally inserted `S-Enter` on this tmux version; it does not establish what an actual terminal's Shift+Enter sends. See the pane fixtures.
- Captures show empty, typed, slash menu, model-selection modal, pasted placeholder and busy states. These are examples, not a complete or stable parser contract. The slash menu flow accidentally selected the model dialog while attempting `/permissions`; it is still an actual modal capture. A busy capture can contain a huge amount of submitted text above the status, and an empty-looking composer can coexist with a busy status. Unrecognized or ambiguous states must remain unknown, not idle.
- Initially the fixture listener was absent: after submitting the long paste, Codex displayed `Reconnecting... waiting for network` and the rollout already contained the full user item. Once the listener was started, the fixture saw that item. This is one connection-refused case, not proof for every network error, crash or stall. The user's item timestamp was `12:20:18.338Z`; the successful response was later, at `12:20:42.369Z`.
- Four independent fresh sessions sent `kill-probe-N`, Enter, then SIGKILL to the launched TUI process after delays of 0, 10, 50 and 200 ms. None had created a rollout file by the subsequent 400 ms inspection. These tests do **not** establish whether the TUI had accepted the input before SIGKILL; they demonstrate that sending Enter alone is not evidence of a durable record. They cannot prove all accepted input is recorded before inference or failure.

**R26 decision:** the universal durability premise is unproven. Set automatic retype to false. An absent rollout record and an empty-looking composer cannot authorize a retry; only a separate proof that the particular input was not taken may authorize retyping. Otherwise use the D7 failure path. These measurements do not resolve all possible terminal key encodings, filesystem durability, or arbitrary process-kill timing.

## Reproduction

The disposable run files are under `/tmp/thc-tmux-spike-1793449181` on the machine that ran the experiment. The local server is `server.py`; it saves request bodies to `requests.jsonl` and uses no authentication. Its configuration was:

```toml
model = "fixture-model"
model_provider = "fixture"
approval_policy = "never"
check_for_update_on_startup = false
[model_providers.fixture]
name = "fixture"
base_url = "http://127.0.0.1:18991/v1"
wire_api = "responses"
requires_openai_auth = false
```

Create a fresh temporary directory and choose an unused loopback port and unique tmux server name. Start a local fixture server like the one described above, and launch `tmux -L NAME new-session -d -x 100 -y 32 -c WORK 'exec env CODEX_HOME=HOME TERM=xterm-256color codex --no-daemon -a never'`. Capture with `tmux -L NAME capture-pane -p -t 0`; send literal text with `send-keys -l`, keys with `send-keys`, and a complete paste with `load-buffer FILE` followed by `paste-buffer -p -t 0`. The envelope used here was `[Telegram 2026-10-31 12:34:56 UTC; bridge example]\nfirst line\n`, followed by 10,000 `x` characters and `\nlast line\n`. Press Enter once, then compare its rollout `response_item` user content and the provider request's corresponding `input` user content, accounting for the trimmed final LF. To reproduce connection refusal, start the listener only after observing reconnect and the rollout. For the kill race, `killprobe.py` creates a fresh home/server per trial, sends a unique literal string and Enter, then SIGKILLs the tmux pane PID at the listed delays. Clean up only the processes and tmux servers launched for the experiment.

Pane captures have only the temporary workspace path replaced by `<WORKSPACE>`; rollout and provider fixtures contain only selected synthetic user records and task boundary events. The fixture provider request excerpt intentionally excludes configuration, environment context, and headers.

## Payload whitespace and Unicode

A second isolated fixture run wrapped a synthetic payload in
`[timestamp]\n<telegram-message>\n` and `\n</telegram-message>`. The
16,927-byte envelope appeared as exactly one matching user record in both
rollout and the provider request. Extracting the 16,865-byte inner payload
preserved leading/trailing blank lines, spaces, Unicode and emoji byte for
byte (SHA-256
`543daaf593dc31a437730ccd30f6733672e6abeb78632e6aa7d7e72c77613128`).
The [original envelope](../../tests/fixtures/codex-tmux/wrapped/envelope.txt),
[payload](../../tests/fixtures/codex-tmux/wrapped/payload.txt),
[rollout excerpt](../../tests/fixtures/codex-tmux/wrapped/rollout-excerpt.json)
and [provider excerpt](../../tests/fixtures/codex-tmux/wrapped/provider-excerpt.json)
are synthetic and retained. An additional title-generation request was
separate from the user submission. This does not establish the R26
durability premise; automatic retyping remains disabled.

## Padded short paste

A further isolated run used Codex CLI 0.166.0, a dedicated tmux server,
a fresh home and a local fixture provider. A short synthetic framed message
was padded with spaces **after** `</telegram-message>` to 1,001 characters.
The composer displayed the large-paste placeholder. Codex expands that
placeholder before trimming the submitted text. The first provider request and
the rollout each contained exactly one matching user item; their extracted text
was byte-identical to the original unpadded envelope, including its tab, blank
line and trailing spaces inside the frame. A separate provider request was
observed; it was not counted as another rollout user item. The synthetic
[synthetic envelope](../../tests/fixtures/codex-tmux/padded/envelope.txt),
[physical paste](../../tests/fixtures/codex-tmux/padded/physical-paste.json)
and [provider text](../../tests/fixtures/codex-tmux/padded/provider-text.txt)
record the compared bytes. This is a measured result for the fixture and CLI
version, not a guarantee for other versions. Codex also normalizes carriage
returns and sanitizes control characters other than tabs and newlines, so the
probe does not establish preservation of those characters.
