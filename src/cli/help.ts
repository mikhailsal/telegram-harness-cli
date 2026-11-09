const descriptions: Record<string, [string, string, string]> = {
  init: [
    "Set up this project's bot, or resume its existing bridge",
    "thc init",
    "thc init --token-file /secure/bot-token --user-id 123 --sandbox workspace-write --no-start",
  ],
  start: [
    "Start the bridge in this folder",
    "thc start",
    "thc start --dir /srv/agent",
  ],
  stop: [
    "Stop the bridge owning this folder",
    "thc stop",
    "thc stop --dir /srv/agent",
  ],
  status: ["Show bridge status", "thc status", "thc status --json"],
  doctor: [
    "Inspect configuration and dependencies",
    "thc doctor",
    "thc doctor --json",
  ],
  tail: ["Show the latest bridge log lines", "thc tail", "thc tail --lines 50"],
  render: [
    "Render Markdown with the bridge's visibility rules",
    "thc render response.md",
    "thc render < response.md",
  ],
  search: [
    "Search visible conversation history",
    'thc search "deployment retries"',
    'thc search "codex-tmux" --any --role user',
  ],
  around: [
    "Show messages around a time",
    'thc around "2026-11-07 18:00:00"',
    'thc around "2026-11-07 18:00:00" 20',
  ],
  day: [
    "Show messages for a day",
    "thc day 2026-11-07",
    "thc day 2026-11-07 --json",
  ],
  explain: [
    "Explain a stable error code",
    "thc explain E_CONFIG",
    "thc explain E_NEEDS_INPUT",
  ],
  attach: [
    "Attach to the live agent session",
    "thc attach",
    "thc attach --dir /srv/agent",
  ],
};
export function help(command: string): string {
  const entry = descriptions[command];
  if (!entry)
    return 'Usage: thc <command> [options]\n\nCommands: init, start, stop, status, doctor, tail, render, search, around, day, explain, attach\n\nExamples:\n  thc status\n  thc search "deployment retries"';
  const options =
    command === "init"
      ? "\n\nInteractive: hidden bot token, owner ID, and explicit sandbox choice.\nReruns keep configuration and version; running agents are left running.\nOptions:\n  --token-file PATH   Use a private token file (mode 0600)\n  --user-id ID        Authorize this Telegram user\n  --sandbox MODE     read-only | workspace-write | danger-full-access\n  --no-start         Prepare without starting the service\n  --update           Select this CLI's package version explicitly\n  --to VERSION       Select an already installed compatible version\n  --force            Replace managed project instructions\n  --dir PATH         Set the target project directory"
      : "";
  return `Usage: thc ${command} [options]\n\n${entry[0]}\n\nExamples:\n  ${entry[1]}\n  ${entry[2]}${options}`;
}
export function isCommand(command: string): boolean {
  return command in descriptions;
}
