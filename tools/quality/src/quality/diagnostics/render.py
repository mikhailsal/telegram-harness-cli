"""Human and machine renderings of an outcome."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from quality.diagnostics.model import Diagnostic, Outcome

REPORT_SCHEMA_VERSION = 1
# C0 and C1 controls, DEL, and the surrogates that stand for undecodable path bytes.
UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
SURROGATE_ESCAPES = range(0xDC80, 0xDD00)


def _escape(match: re.Match[str]) -> str:
    code = ord(match.group())
    if code in SURROGATE_ESCAPES:
        return f"\\x{code - 0xDC00:02x}"
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"


def printable(text: str) -> str:
    """Escape control characters and undecodable bytes so text cannot drive a terminal."""
    return UNPRINTABLE.sub(_escape, text)


def diagnostic_to_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    """Convert one diagnostic to a JSON-compatible mapping."""
    location = diagnostic.location
    return {
        "rule": diagnostic.rule,
        "severity": diagnostic.severity.value,
        "kind": diagnostic.kind.value,
        "message": diagnostic.message,
        "path": location.path if location else None,
        "line": location.line if location else None,
        "column": location.column if location else None,
        "component": diagnostic.component,
        "hint": diagnostic.hint,
        "data": dict(sorted(diagnostic.data.items())),
    }


def outcome_to_json(
    outcome: Outcome, *, command: str, extra: Mapping[str, Any] | None = None
) -> str:
    """Render an outcome as a stable, ASCII-only JSON document."""
    document = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "command": command,
        "exit_code": int(outcome.exit_code()),
        "summary": {
            "errors": len(outcome.errors),
            "warnings": len(outcome.warnings),
            "total": len(outcome.diagnostics),
        },
        "diagnostics": [diagnostic_to_dict(d) for d in outcome.diagnostics],
        **(extra or {}),
    }
    return json.dumps(document, indent=2, sort_keys=False, ensure_ascii=True) + "\n"


def render_text(outcome: Outcome) -> str:
    """Render an outcome for a terminal, one finding per paragraph."""
    lines: list[str] = []
    for diagnostic in outcome.diagnostics:
        where = diagnostic.location.render() + ": " if diagnostic.location else ""
        scope = f" [{diagnostic.component}]" if diagnostic.component else ""
        lines.append(
            printable(
                f"{where}{diagnostic.severity.value}: {diagnostic.message} ({diagnostic.rule})"
                f"{scope}"
            )
        )
        if diagnostic.hint:
            lines.append(printable(f"  hint: {diagnostic.hint}"))
    errors, warnings = len(outcome.errors), len(outcome.warnings)
    verdict = "FAILED" if outcome.exit_code() else "PASSED"
    lines.append(f"{verdict}: {errors} error(s), {warnings} warning(s)")
    return "\n".join(lines) + "\n"
