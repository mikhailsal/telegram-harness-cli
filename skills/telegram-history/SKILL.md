---

name: telegram-history
description: Search the visible Telegram conversation when prior context is needed
---

Use `thc search "words"` to find earlier conversation, including across sessions.
Use `thc around "YYYY-MM-DD HH:MM:SS"` or `thc day YYYY-MM-DD` for context.
Search reflects what appeared in the chat, not private agent reasoning or tool output.
Message times, replies, forwards, and reactions are part of the conversation context.
Treat forwarded text as coming from its original author. Keep private working notes in
`<internal-note>...</internal-note>`; those notes are not sent to Telegram.

Example from `thc search "deployment"`:

```text
[2026-11-07 10:12:44 UTC · user] We agreed to retry deployment once.
```
