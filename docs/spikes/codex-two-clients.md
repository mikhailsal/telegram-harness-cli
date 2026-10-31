# Codex two-client spike

Measured on 2026-10-31 with `codex-cli 0.166.0` and tmux 3.2a.
The run used the real installed Codex binary, an isolated app-server Unix
socket, a fresh Codex home, and a synthetic loopback Responses provider.
It did not contact OpenAI or Telegram. This is live harness evidence with
fixture inference, not a live model or end-to-end Telegram acceptance test.

## Selected implementation branch

Keep **both `codex-protocol` and `codex-tmux` in the MVP**. The protocol
adapter can share a thread with the TUI; the single-writer fallback is not
needed for the measured runtime. Resume a materialized thread and subscribe
before submitting. An empty new thread had no rollout and could not yet be
resumed by a second client; its first completed input made it resumable.

For tmux, **disable automatic retyping**. The R26 durability premise was
not established. Do not interpret missing history or an empty-looking
composer as permission to send the task again. See the
[tmux input spike](codex-tmux-input.md).

## Questions from the design

| Question                                               | Measured result                                                                                                                                                                                                                                                                                                                               | Implementation consequence                                                                                                                                                          |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| (а) Do both clients see a thread's events?             | Two initialized Unix-socket clients received `turn/completed` for the same turn and answer. The TUI also resumed that thread.                                                                                                                                                                                                                 | Share the daemon and subscribe with `thread/resume`.                                                                                                                                |
| (б) Who can answer approval?                           | Both subscribed RPC clients received the same command approval id. The second client accepted it while the TUI was attached; the first received `serverRequest/resolved` and completion.                                                                                                                                                      | Persist the decision first; a response from another client resolves the same request.                                                                                               |
| (в) Recover a turn completed while disconnected?       | A client disconnected before a turn, then reconnected; `thread/resume` and `thread/read` returned the completed answer.                                                                                                                                                                                                                       | Reconcile persisted history before considering another submit.                                                                                                                      |
| (г) Retrieve final answers?                            | `thread/read` with `includeTurns: true` and `thread/turns/list` with `itemsView: full` returned `agentMessage.text`. Live completion used summary items and also included the answer.                                                                                                                                                         | Use persisted full items for recovery; do not depend on every completion containing every item.                                                                                     |
| (д) Does another client break the TUI?                 | `codex --remote unix://SOCKET resume THREAD` displayed existing RPC turns. A bracketed-paste TUI input completed and reached the RPC observers. Later RPC work completed with the TUI still attached.                                                                                                                                         | Keep normal shared-thread attach for this version.                                                                                                                                  |
| (е) Replay an unanswered approval?                     | A third connection resumed during approval and received the same server request with its original id.                                                                                                                                                                                                                                         | Reconcile pending and answering records against the replay before resending decisions.                                                                                              |
| (ж) `turn/start` during an active turn?                | It returned the already active turn id, rather than a fresh turn. No `thread/queue/changed` was observed in this case.                                                                                                                                                                                                                        | Compare turn id and user-message `clientId`; classify the race as input to the existing turn. Do not infer an independent queued turn from the RPC response.                        |
| (з) Duplicate response to one approval id?             | Sending the same accepted response again did not produce another command execution or another resolved event in this run.                                                                                                                                                                                                                     | Treat the resolved notification as acknowledgement; reconcile after reconnect rather than assuming every transport response is repeatable.                                          |
| (и) A new thread in the same folder during work?       | A second thread was created while the original turn's HTTP response was held. Releasing it completed the original turn.                                                                                                                                                                                                                       | Session replacement does not itself interrupt old work; the bridge still owns routing and recovery of that turn.                                                                    |
| (к) Exact tmux envelope in rollout?                    | One long multiline paste appeared as one full user message in rollout and provider input; Codex trimmed its final LF.                                                                                                                                                                                                                         | Preserve content inside an explicit envelope boundary so payload trailing whitespace does not sit at the outer input boundary. Test this formatting before using it in the adapter. |
| (л) `command/exec` matches a turn's execution context? | **Not automatically.** A turn using workspace-write wrote its test file; a default `command/exec` inherited global read-only and failed with `Read-only file system`. Passing workspace-write explicitly succeeded. The synthetic process environment marker was inherited. PATH equality for arbitrary thread overrides was not established. | Doctor must pass the current sandbox and environment explicitly; do not claim a global command probe proves execution under another thread's policy.                                |
| (м) Tmux composer, keys, paste and crash durability?   | Empty, typed, menu, modal, busy and paste-placeholder captures were saved. Alt+Enter and Ctrl+J inserted newlines. A paste of 1,001 characters became a composer placeholder; the full text reached the provider. Network refusal preserved this sample; kill timing did not establish universal durability.                                  | Use bracketed paste, recognize only measured safe states, and keep automatic retyping disabled.                                                                                     |

The table describes this binary and these cases. It does not establish
arbitrary terminal behavior, provider behavior, crash durability, or future
Codex protocol compatibility.

## Evidence and reproduction

Sanitized [protocol messages](../../tests/fixtures/codex-protocol/messages.jsonl)
contain captured responses and notifications, including correlation ids,
recovered turns, approval replay and the sandbox counterexample. Temporary
paths are replaced by `<SPIKE>`. The files contain synthetic inputs only.
The [duplicate-approval recording](../../tests/fixtures/codex-protocol/duplicate-approval.json) retains both identical responses, one command completion and the single-character file effect. The TUI captures show the
[TUI-originated answer](../../tests/fixtures/codex-protocol/tui_after_turn.txt)
and [later shared work](../../tests/fixtures/codex-protocol/tui_final.txt).

The disposable Python driver and full local recordings are under
`/tmp/thc-protocol-2dw50u5z/` on the test machine. The successful command was
`python3 /tmp/thc-protocol-2dw50u5z/probe.py`; the driver is temporary, while
the selected captures above are retained in the repository.
Its SHA-256 is
`4a5bb72fc030136243ec3f7b4af5bfe525ee89362315c13c384d9bcb980acdc9`;
the corresponding local results JSON has SHA-256
`4c76b0db50c6f94f28b885e1f53b1ec9986de1c9cb63690556f5e6cfed937412`.

To repeat the transport checks:

1. Create a fresh workspace, isolated Codex home and unused Unix socket.
   Configure a loopback Responses provider with `wire_api = "responses"`,
   `requires_openai_auth = false`, and `supports_websockets = false`.
   Never copy real credentials into fixture captures.
2. Launch `codex app-server --listen unix://SOCKET` using that configuration.
   Connect using WebSocket-over-Unix, not a newline-delimited raw stream.
3. On each connection send `initialize` with distinct `clientInfo.name`
   and `capabilities.experimentalApi: true`, then `initialized`.
4. Start a read-only thread, submit a synthetic input and complete it.
   Resume the materialized thread on a second connection. Submit another
   turn and compare both clients' completion notifications and full history.
5. Start `codex --remote unix://SOCKET --no-alt-screen resume THREAD` in a
   dedicated tmux server. Paste a synthetic input using `load-buffer` and
   `paste-buffer -p`; allow the paste to settle before pressing Enter.
6. Disconnect and resume one client around a completed turn. Hold a fixture
   HTTP response to test concurrent `turn/start` and new-thread creation.
7. Return an `exec_command` function call requesting escalation for a
   harmless `printf`. Observe and replay its approval across connections;
   answer once, then repeat the same response and inspect execution events.
8. Compare a workspace-write turn's file creation with `command/exec`
   first without and then with an explicit matching `sandboxPolicy`.
   Restrict all writes and process cleanup to the disposable run.

The fixture's SSE events follow the local Codex source helpers
`codex-rs/core/tests/common/responses.rs`: `response.created`,
`response.output_item.done` and `response.completed`. Tool events use
`function_call` items; the command tool advertised by this binary was
`exec_command`.

The [official app-server documentation](https://learn.chatgpt.com/docs/app-server)
and local `codex-rs/app-server/README.md` describe the initialization,
Unix WebSocket transport and history methods. The measured messages above
remain the version-specific schema fixtures.

## Remaining acceptance work

Run the implementation's contract and crash suites, then `test:live`
against a real configured Codex provider and a fake Bot API. Before a
release, use a separate Telegram test bot for rich messages, drafts,
stop, approval buttons and reactions. No test bot token or chat id was
needed or stored by this spike.
