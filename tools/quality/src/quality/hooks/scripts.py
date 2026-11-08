"""The text of the managed Git hooks; each one only calls the `quality` launcher."""

from __future__ import annotations

from enum import StrEnum

MARKER = "# quality-managed-hook: "
VERSION = "v1"


class HookName(StrEnum):
    """The Git hooks that `quality bootstrap` manages."""

    PRE_COMMIT = "pre-commit"
    PRE_PUSH = "pre-push"


def _header(name: HookName) -> str:
    return (
        "#!/bin/sh\n"
        f"{MARKER}{name} {VERSION}\n"
        "# Installed by `quality bootstrap` and replaced by the next bootstrap; do not edit.\n"
        f"# Put your own hook in {name}.local next to this file; it runs first.\n"
        "set -eu\n"
        f'local_hook="$(dirname "$0")/{name}.local"\n'
    )


PRE_COMMIT_BODY = """\
if [ -x "$local_hook" ]; then
    "$local_hook" "$@"
fi
# Git runs hooks from the root of the work tree, so this is the repository's launcher.
exec ./quality check --staged
"""

# Git writes one line per pushed ref: <local ref> <local sha> <remote ref> <remote sha>.
# The loop reads a here-document, not a pipe, so a failing check ends the hook itself.
PRE_PUSH_BODY = """\
updates=$(cat)
if [ -x "$local_hook" ]; then
    printf '%s\\n' "$updates" | "$local_hook" "$@"
fi
while read -r _ local_sha _ _; do
    [ -n "$local_sha" ] || continue
    case "$local_sha" in
        *[!0]*) ./quality check --rev "$local_sha" --all || exit $? ;;
    esac
done <<EOF
$updates
EOF
"""

BODIES = {HookName.PRE_COMMIT: PRE_COMMIT_BODY, HookName.PRE_PUSH: PRE_PUSH_BODY}


def script(name: HookName) -> str:
    """Return the exact text of a managed hook."""
    return _header(name) + BODIES[name]


def is_managed(name: HookName, content: bytes) -> bool:
    """Whether a hook file carries this hook's managed marker on its second line."""
    lines = content.split(b"\n", 2)
    return len(lines) > 1 and lines[1].startswith(f"{MARKER}{name} ".encode())
