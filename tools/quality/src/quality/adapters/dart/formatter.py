"""`dart format`, the formatter of the Dart profile.

The pinned SDK checks the named files with `--output=none
--set-exit-if-changed`, which prints `Changed <file>` for each file it would
rewrite and exits 1. A file it cannot parse makes it exit 65 after checking the
others; the analyzer reports the syntax error, so only an exit 65 that says so
is accepted. The formatter honors the component's `formatter` settings in
analysis_options.yaml, which the candidate controls like any other style.
"""

from __future__ import annotations

import re
from pathlib import Path

from quality.adapters.dart.environment import Sdk
from quality.adapters.native import fail, run_tool
from quality.adapters.target import Target
from quality.diagnostics import Diagnostic, Location, Rule

FORMAT = Rule(
    id="dart.format",
    title="Dart file is not formatted by the pinned SDK",
    requirement="R04",
    explanation=(
        "Source and test files are checked with `dart format --output=none "
        "--set-exit-if-changed` of the pinned SDK."
    ),
    remedy="Run `./quality fix`, review the change, and stage it.",
)
RULES = (FORMAT,)

TIMEOUT = 600.0
TOOL = "dart format"
UNPARSABLE_EXIT = 65
CHANGED = re.compile(r"^Changed (.+)$", re.MULTILINE)
UNPARSABLE = "Could not format because the source could not be parsed"


def dart_format(target: Target, *, sdk: Sdk, home: Path) -> list[Diagnostic]:
    """Report each named file whose formatting differs from the formatter's."""
    argv = sdk.command("format", "--output=none", "--set-exit-if-changed", *target.arguments())
    completed = run_tool(
        sdk.run_in(argv, target.directory, home, timeout=TIMEOUT),
        name=TOOL,
        accepted={0, 1, UNPARSABLE_EXIT},
    )
    output = completed.stderr_text() + completed.stdout_text()
    if completed.returncode == UNPARSABLE_EXIT and UNPARSABLE not in output:
        fail(TOOL, f"exit status {UNPARSABLE_EXIT}: {output}")
    return [
        FORMAT.diagnostic(
            "the file is not formatted; run `./quality fix`",
            location=Location(target.relative(TOOL, name)),
            component=target.component.id,
        )
        for name in CHANGED.findall(completed.stdout_text())
    ]
