"""PHPStan, the type checker of the PHP profile.

The component's locked PHPStan analyses the named files at its highest level
under the gate's settings (`gate.py`), with no baseline and none of the
component's `phpstan.neon` files. Every reported error is a `php.types` finding
whose symbol is PHPStan's error identifier, so a reviewed exception in
quality.toml names the file and the identifier rather than silencing the file
(decision 0013); inline `@phpstan-ignore` comments are suppressions, which the
common gates reject.

PHPStan loads the component's Composer autoloader, which runs the files of
`autoload.files`, so it runs behind the network isolation of unit suites where
the system allows it (decision 0008). A file that cannot be parsed is reported
with the identifier `phpstan.parse`, and PHPStan then reports no other file.
Errors outside any file, such as an invalid setting, make the run a failure.
"""

from __future__ import annotations

from pathlib import Path

from quality.adapters import native
from quality.adapters.native import fail, run_tool
from quality.adapters.php.runtime import Php
from quality.adapters.target import Entry, Target, document, findings, location, number
from quality.boundedio import JsonValue
from quality.diagnostics import Diagnostic, Rule
from quality.process import Command

TYPES = Rule(
    id="php.types",
    title="PHPStan error at the highest level",
    requirement="R04",
    explanation=(
        "Source and test files are analysed by the component's locked PHPStan at level max "
        "with the gate's own settings and no baseline. Every error fails, syntax errors "
        "included."
    ),
    remedy=(
        "Fix the error. For a specific incompatibility that cannot be fixed, add a reviewed "
        "exception with the file and the error identifier as symbol; never a baseline."
    ),
)
RULES = (TYPES,)

TIMEOUT = 1800.0
TOOL = "phpstan"
BINARY = "vendor/phpstan/phpstan/phpstan"
MEMORY = "--memory-limit=2G"


def _messages(target: Target, name: str, value: JsonValue) -> list[Diagnostic]:
    if not isinstance(value, dict):
        fail(TOOL, f"the entry of {name} is not an object")
    path = target.relative(TOOL, name)
    return [_diagnostic(target, path, message) for message in findings(TOOL, value.get("messages"))]


def _diagnostic(target: Target, path: str, message: Entry) -> Diagnostic:
    text, identifier = message.get("message"), message.get("identifier")
    if not isinstance(text, str):
        fail(TOOL, "an error has no message")
    shown = f"[{identifier}] {text}" if isinstance(identifier, str) else text
    return TYPES.diagnostic(
        shown,
        location=location(path, number(message, "line") or None, None),
        component=target.component.id,
        data={"symbol": identifier} if isinstance(identifier, str) else None,
    )


def phpstan(target: Target, *, php: Php, settings: Path, home: Path) -> list[Diagnostic]:
    """Report PHPStan's errors in the named files."""
    argv = php.command(
        str(target.directory / BINARY),
        "analyse",
        f"--configuration={settings}",
        "--level=max",
        "--error-format=json",
        "--no-progress",
        "--no-interaction",
        MEMORY,
        "--",
        *target.arguments(),
    )
    env = php.environ(home)
    command = Command(
        native.contained(argv)[0], cwd=target.directory, timeout=TIMEOUT, env=env, reap=True
    )
    completed = run_tool(command, name=TOOL, accepted={0, 1})
    report = document(TOOL, completed)
    if not isinstance(report, dict):
        fail(TOOL, "output is not a JSON object")
    files, general = report.get("files"), report.get("errors")
    if not isinstance(files, dict):
        fail(TOOL, "the report has no 'files' object")
    if general:
        fail(TOOL, f"errors outside the files: {general}")
    found = [item for name, value in files.items() for item in _messages(target, name, value)]
    if completed.returncode == 1 and not found:
        fail(TOOL, f"exit status 1 without errors: {completed.stderr_text()}")
    return found
