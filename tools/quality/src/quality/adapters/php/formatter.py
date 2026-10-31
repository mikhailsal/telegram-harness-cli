"""PHP CS Fixer, the formatter of the PHP profile.

The component's locked PHP CS Fixer checks the named files with `check
--format=json` under the gate's settings (`gate.py`), sequentially and without
a cache. The report lists each file it would rewrite, relative to the component
directory, and the exit status adds 8 for those and 4 for a file it cannot
parse, which it skips; PHPStan reports the syntax error.
"""

from __future__ import annotations

from pathlib import Path

from quality.adapters.native import fail, run_tool
from quality.adapters.php.runtime import Php
from quality.adapters.target import Target, document, findings
from quality.diagnostics import Diagnostic, Location, Rule

FORMAT = Rule(
    id="php.format",
    title="PHP file does not follow PER Coding Style",
    requirement="R04",
    explanation=(
        "Source and test files are checked with the component's locked PHP CS Fixer under the "
        "gate's own settings: the @PER-CS rule set without risky rules. The component's "
        ".php-cs-fixer files are not read."
    ),
    remedy="Run `./quality fix`, review the change, and stage it.",
)
RULES = (FORMAT,)

TIMEOUT = 900.0
TOOL = "php-cs-fixer"
BINARY = "vendor/friendsofphp/php-cs-fixer/php-cs-fixer"
# 0 is clean; 8 means files need fixing and 4 that some could not be parsed.
ACCEPTED = frozenset({0, 4, 8, 12})


def cs_fixer(php: Php, target: Target, settings: Path, command: str) -> tuple[str, ...]:
    """Return a PHP CS Fixer command over the named files."""
    return php.command(
        str(target.directory / BINARY),
        command,
        f"--config={settings}",
        "--using-cache=no",
        "--format=json",
        "--show-progress=none",
        "--no-interaction",
        "--sequential",
        "--",
        *target.arguments(),
    )


def cs_check(target: Target, *, php: Php, settings: Path, home: Path) -> list[Diagnostic]:
    """Report each named file that the formatter would rewrite."""
    argv = cs_fixer(php, target, settings, "check")
    completed = run_tool(
        php.run_in(argv, target.directory, home, timeout=TIMEOUT), name=TOOL, accepted=ACCEPTED
    )
    report = document(TOOL, completed)
    if not isinstance(report, dict):
        fail(TOOL, "output is not a JSON object")
    return [
        FORMAT.diagnostic(
            "the file does not follow PER Coding Style; run `./quality fix`",
            location=Location(target.relative(TOOL, entry.get("name"))),
            component=target.component.id,
        )
        for entry in findings(TOOL, report.get("files"))
    ]
