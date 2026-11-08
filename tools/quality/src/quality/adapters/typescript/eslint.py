"""ESLint with typescript-eslint, the linter of the TypeScript profile.

Before ESLint runs, the helper asks it for the configuration of every named
file: a file without one, or one linted without type information
(`projectService` or `project`), fails, because a configuration can otherwise
lint nothing or only the rules that need no types. So does a file of a browser
component that lacks one of the rules its target requires, at warning or error
level (decision 0017). ESLint then runs on the
explicit file list with `--no-ignore`, and bulk suppressions are read from an
empty file in the work area instead of the candidate's
`eslint-suppressions.json`. Every message is an error, warnings included, and
so is an unused disable directive; parse errors are messages too.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from quality.adapters.native import run_tool
from quality.adapters.target import Entry, Target, document, findings, location, number
from quality.adapters.typescript import helper
from quality.adapters.typescript.environment import Node
from quality.boundedio import JsonValue
from quality.diagnostics import Diagnostic, Rule
from quality.process import Command

LINT = Rule(
    id="typescript.lint",
    title="ESLint finding",
    requirement="R04",
    explanation=(
        "Source and test files are linted by the pinned ESLint with the component's flat "
        "configuration, which must give every file type information. In a browser "
        "component it must also enable the React Hooks rules and the selected "
        "jsx-a11y-x rules for the source files, and @typescript-eslint/no-floating-promises "
        "for the tests. Ignore patterns, bulk suppressions, and warning levels do not apply."
    ),
    remedy="Fix the finding; `./quality fix` applies ESLint's fixes to the working tree.",
)
RULES = (LINT,)

TIMEOUT = 900.0
TOOL = "eslint"
OPTIONS = (
    "--format",
    "json",
    "--no-ignore",
    "--report-unused-disable-directives-severity",
    "error",
)


def _configuration(file: helper.Entry) -> str | None:
    if helper.flag(file, "ignored") or not helper.flag(file, "configured"):
        return "no ESLint configuration lints this file; every source and test file is linted"
    if not helper.flag(file, "typed"):
        return (
            "ESLint lints this file without type information; "
            "set parserOptions.projectService in eslint.config.*"
        )
    return None


def _disabled(file: helper.Entry, required: tuple[str, ...]) -> str | None:
    if not required:
        return None
    enabled = {
        helper.text(entry, "rule")
        for entry in helper.entries(file, "rules")
        if helper.integer(entry, "severity") > 0
    }
    missing = [rule for rule in required if rule not in enabled]
    if not missing:
        return None
    return f"the browser target requires {', '.join(missing)}, which ESLint does not enable here"


def eslint_configuration(
    target: Target,
    *,
    node: Node,
    work: Path,
    program: Path = helper.HELPER,
    required: Mapping[str, tuple[str, ...]] | None = None,
) -> list[Diagnostic]:
    """Report named files that ESLint would not lint with type information or `required` rules.

    `required` maps a repository path to the rules its configuration must enable.
    """
    needed = required or {}
    request: dict[str, JsonValue] = {
        "eslint": str(target.directory),
        "cwd": str(target.directory),
        "files": [*target.arguments()],
        "rules": [*sorted({rule for rules in needed.values() for rule in rules})],
    }
    response = helper.run_helper(
        helper.Program(node, program), "eslint-config", request, work=work, cwd=target.directory
    )
    found: list[Diagnostic] = []
    for file in helper.entries(response, "files"):
        path = target.relative(helper.TOOL, helper.text(file, "file"))
        message = _configuration(file) or _disabled(file, needed.get(path, ()))
        if message is not None:
            found.append(
                LINT.diagnostic(
                    message, location=location(path, None, None), component=target.component.id
                )
            )
    return found


def _message(entry: Entry) -> str:
    rule = entry.get("ruleId")
    text = str(entry.get("message", "")).strip()
    return f"{rule}: {text}" if isinstance(rule, str) and rule else text


def eslint_lint(target: Target, *, node: Node, script: Path, work: Path) -> list[Diagnostic]:
    """Lint the named files; every message is an error."""
    work.mkdir(parents=True, exist_ok=True)
    suppressions = work / "eslint-suppressions.json"
    suppressions.write_text("{}", encoding="utf-8")
    argv = (
        str(node.executable),
        str(script),
        *OPTIONS,
        "--suppressions-location",
        str(suppressions),
        *target.arguments(),
    )
    home = work / "typescript-home" / "eslint"
    command = Command(argv, cwd=target.directory, timeout=TIMEOUT, env=node.environ(home))
    completed = run_tool(command, name=TOOL, accepted={0, 1})
    found: list[Diagnostic] = []
    for result in findings(TOOL, document(TOOL, completed)):
        path = target.relative(TOOL, result.get("filePath"))
        found.extend(
            LINT.diagnostic(
                _message(message),
                location=location(path, number(message, "line"), number(message, "column")),
                component=target.component.id,
            )
            for message in findings(TOOL, result.get("messages", []))
        )
    return found
