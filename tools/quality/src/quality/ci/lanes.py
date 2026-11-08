"""The lanes of the gate, which CI runs in separate required jobs (decision 0018).

The complete gate is both lanes. The browser lane checks the active browser
components; the main lane runs the common gates and the history checks and
checks every other component. Each component belongs to exactly one lane, so
the two results together are the complete gate, and a required workflow that
runs one lane must run the other in a required job too.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Iterator
from enum import StrEnum

from quality.ci.gatecommands import gate_arguments
from quality.ci.workflow import Table, finding, mapping, steps
from quality.diagnostics import Diagnostic, Rule
from quality.policy import Component

LANES = Rule(
    id="ci.lanes",
    title="The required jobs do not run every lane of the gate",
    requirement="R09",
    explanation=(
        "`quality gate --lane main` runs the common gates and the history checks and "
        "checks every component that does not run in a browser; `--lane browser` checks "
        "the browser components. When a required job runs one lane, another required job "
        "must run the other, or a browser component could pass without its scenarios."
    ),
    remedy=(
        "Run both lanes, or the gate without --lane, in required GitHub or GitLab jobs; "
        "use literal lanes or locally defined lane variables."
    ),
)
RULES = (LANES,)

# Broad discovery only; gate_arguments decides whether text proves an executed call.
GATE = re.compile(r"quality[ \t]+gate\b([^\n]*)")
VARIABLE = re.compile(r"\$(?:([A-Za-z_][A-Za-z_0-9]*)|\{([A-Za-z_][A-Za-z_0-9]*)\})")
UNRESOLVED = "<unresolved>"


class Lane(StrEnum):
    """A part of the gate that one CI job runs."""

    MAIN = "main"
    BROWSER = "browser"


def in_lane(component: Component, lane: Lane | None) -> bool:
    """Whether a lane checks the component; the complete gate, None, checks every one."""
    return lane is None or component.in_browser is (lane is Lane.BROWSER)


def _lane_value(value: str, variables: Table, seen: tuple[str, ...] = ()) -> str:
    if value[:1] in ("'", '"') and value[-1:] == value[:1]:
        quote, value = value[0], value[1:-1]
        if quote == "'":
            return value
    variable = VARIABLE.fullmatch(value)
    if variable is None:
        return value
    name = variable.group(1) or variable.group(2)
    resolved = variables.get(name)
    if not isinstance(resolved, str) or name in seen:
        return UNRESOLVED
    return _lane_value(resolved, variables, (*seen, name))


def _lane_argument(argument: str) -> str:
    if argument[:1] not in ("'", '"') or argument[-1:] != argument[:1]:
        return argument
    quote, unquoted = argument[0], argument[1:-1]
    if unquoted.startswith("--lane="):
        return f"--lane={quote}{unquoted.partition('=')[2]}{quote}"
    return unquoted if unquoted == "--lane" else argument


def _call_lane(text: str, variables: Table) -> str | None:
    try:
        arguments = [_lane_argument(argument) for argument in shlex.split(text, posix=False)]
    except ValueError:
        return UNRESOLVED
    options = [index for index, arg in enumerate(arguments) if arg.split("=", 1)[0] == "--lane"]
    if not options:
        return None
    if len(options) != 1:
        return UNRESOLVED
    index = options[0]
    option = arguments[index].partition("=")
    value = option[2] if option[1] else next(iter(arguments[index + 1 :]), "")
    return _lane_value(value, variables)


def gate_lanes(script: str, variables: Table | None = None) -> Iterator[str | None]:
    """Read literal or local-variable lanes; unresolved lane options never mean all lanes."""
    for text in gate_arguments(script):
        yield UNRESOLVED if text is None else _call_lane(text, variables or {})


def _job_lanes(job: Table) -> Iterator[str | None]:
    for step in steps(job):
        run = step.get("run")
        if isinstance(run, str):
            variables = {**(mapping(job.get("env")) or {}), **(mapping(step.get("env")) or {})}
            yield from gate_lanes(run, variables)


def _invalid_lanes(path: str, found: set[str | None]) -> Iterator[Diagnostic]:
    unknown = (str(value) for value in found if value is not None and value not in Lane)
    for lane in sorted(unknown):
        message = f"the required jobs have an unknown or unresolved gate lane '{lane}'"
        yield finding(LANES, path, message, lane or UNRESOLVED)


def check_lanes(path: str, required: Iterable[Table]) -> Iterator[Diagnostic]:
    """Report unknown lanes and each lane that no required job runs while another runs one."""
    found = {lane for job in required for lane in _job_lanes(job)}
    yield from _invalid_lanes(path, found)
    if not found or None in found:
        return
    ran = ", ".join(sorted(lane for lane in found if lane is not None))
    for lane in Lane:
        if lane.value not in found:
            message = f"the required jobs run the gate's lane {ran} but not '{lane}'"
            yield finding(LANES, path, message, lane.value)
