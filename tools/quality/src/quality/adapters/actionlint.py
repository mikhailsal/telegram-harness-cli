"""GitHub Actions workflow linting with the pinned actionlint and no candidate configuration."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

from quality.adapters.native import fail, run_tool
from quality.boundedio import JsonValue, ReportError, parse_json
from quality.diagnostics import Diagnostic, Location, Rule
from quality.process import Command, sanitized

LINT = Rule(
    id="ci.actionlint",
    title="Workflow rejected by actionlint",
    requirement="R09",
    explanation=(
        "Workflows under .github/workflows are checked with the pinned actionlint: YAML "
        "syntax, the workflow schema, expressions and their contexts, job dependencies, "
        "runner labels, and action inputs. An empty configuration owned by the core replaces "
        "any .github/actionlint.yaml in the candidate; shell scripts are not linted."
    ),
    remedy="Fix the reported workflow problem; `quality explain ci.actionlint` lists the checks.",
)
RULES = (LINT,)

FOUND = 1
TIMEOUT = 300.0


def _text(entry: dict[str, JsonValue], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        fail("actionlint", f"report entry without text field {key}")
    return value


def _number(entry: dict[str, JsonValue], key: str) -> int:
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        fail("actionlint", f"report entry without number field {key}")
    return value


def _findings(report: JsonValue, paths: Sequence[str]) -> Iterator[Diagnostic]:
    if not isinstance(report, list):
        fail("actionlint", "report is not a list")
    for entry in report:
        if not isinstance(entry, dict):
            fail("actionlint", "report entry is not an object")
        path = _text(entry, "filepath")
        if path not in paths:
            fail("actionlint", f"finding outside the linted workflows: {path}")
        kind = _text(entry, "kind")
        yield LINT.diagnostic(
            f"{kind}: {_text(entry, 'message')}",
            location=Location(path, _number(entry, "line"), _number(entry, "column")),
            data={"symbol": kind},
        )


def lint_workflows(
    paths: Sequence[str], root: Path, work: Path, *, actionlint: Path
) -> list[Diagnostic]:
    """Lint workflows by their paths relative to the materialized candidate root."""
    if not paths:
        return []
    config = work / "actionlint.yaml"
    config.write_text("", encoding="utf-8")
    argv = (str(actionlint), "-config-file", str(config), "-format", "{{json .}}")
    argv += ("-shellcheck=", "-pyflakes=", "-no-color", *paths)
    command = Command(argv, cwd=root, timeout=TIMEOUT, env=sanitized())
    completed = run_tool(command, name="actionlint", accepted={0, FOUND})
    if completed.stdout_truncated:
        fail("actionlint", "the report exceeded the output limit")
    try:
        found = list(_findings(parse_json(completed.stdout, "actionlint output"), paths))
    except ReportError as error:
        fail("actionlint", str(error))
    if (completed.returncode == FOUND) != bool(found):
        fail("actionlint", "exit status and report disagree")
    return found
