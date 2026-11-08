"""The TypeScript helper: the compiler API and ESLint, run by the pinned Node (decision 0011).

The helper is a TypeScript program in `tools/quality/helpers/typescript` that
Node runs directly, located relative to this package so a materialized
candidate core uses its own helper. It has no dependencies of its own: it loads
`typescript` and `eslint` the way Node resolves them from the component
directory, so the versions the component locks do the work. A request is a
JSON file; the response is one JSON object on stdout that carries the protocol
version. Exit status 2 is a request the helper cannot serve and 1 an
unexpected failure; both are tool failures, as is any field of an unexpected
type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.adapters import helperio
from quality.adapters.helperio import PROTOCOL, Entry, Fields, repository_path
from quality.adapters.native import fail, run_tool
from quality.adapters.typescript.environment import Node
from quality.boundedio import JsonValue
from quality.process import Command

# src/quality/adapters/typescript/helper.py -> tools/quality
CORE = Path(__file__).resolve().parents[4]
HELPER = CORE / "helpers" / "typescript" / "src" / "main.ts"
TIMEOUT = 1800.0
TOOL = "typescript helper"
FIELDS = Fields(TOOL)
entries = FIELDS.entries
text = FIELDS.text
texts = FIELDS.texts
integer = FIELDS.integer
optional_integer = FIELDS.optional_integer
optional_text = FIELDS.optional_text
flag = FIELDS.flag
optional_entry = FIELDS.optional_entry

__all__ = [
    "PROTOCOL",
    "Entry",
    "Program",
    "entries",
    "flag",
    "integer",
    "optional_entry",
    "optional_integer",
    "optional_text",
    "repository_path",
    "run_helper",
    "text",
    "texts",
]


@dataclass(frozen=True, slots=True)
class Program:
    """The helper script and the pinned Node that runs it."""

    node: Node
    path: Path = HELPER


def run_helper(
    program: Program, command: str, request: Mapping[str, JsonValue], *, work: Path, cwd: Path
) -> Entry:
    """Run one helper command and return its checked response."""
    if not program.path.is_file():
        fail(TOOL, f"the helper is missing at {program.path}")
    handle = helperio.write_request(work / "typescript-requests", command, request)
    argv = (str(program.node.executable), str(program.path), command, str(handle))
    env = program.node.environ(work / "typescript-home" / "helper")
    completed = run_tool(Command(argv, cwd=cwd, timeout=TIMEOUT, env=env), name=TOOL, accepted={0})
    return helperio.read_response(TOOL, command, completed)
