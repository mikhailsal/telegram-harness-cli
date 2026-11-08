# Release checklist

- Run the required repository quality gate and dependency audit.
- Test both Codex adapters against the supported Codex version with a private test bot.
- Verify init, a full Telegram turn, approvals, restart recovery, attachments, and history search.
- Verify `thc doctor`, attach, version selection, and rollback refusal across schema versions.
- Check all `agent-ready-v1` errors and confirm that tokens are absent from logs and traces.
- Record the acceptance-week and manual agent-behavior checks required by the design.
