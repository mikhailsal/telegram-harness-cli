"""A reviewable edit of `quality.toml`: one more component (decision 0016).

The new `[[components]]` table goes after the last component, before the
comments of the table that follows, so the change reads as one block. The
result is parsed again and must equal the old policy plus the new component;
otherwise the table is appended at the end, where TOML adds it to the same
array.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from quality.diagnostics import Diagnostic, Location
from quality.policy.load import POLICY_FILE, POLICY_INVALID, parse_policy
from quality.policy.models import Policy

HEADER = re.compile(r"^\s*\[\[?\s*([A-Za-z0-9_.-]+)\s*\]\]?\s*(?:#.*)?$")


class EditError(ValueError):
    """The edited policy does not parse to the old policy plus the new component."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        super().__init__("; ".join(d.message for d in diagnostics))
        self.diagnostics = diagnostics


@dataclass(frozen=True, slots=True)
class Draft:
    """The fields `quality component add` sets on an application component."""

    id: str
    profile: str
    root: str
    source: tuple[str, ...]
    unit: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    no_boundary_reason: str | None = None
    target: str | None = None
    framework: str | None = None
    bundler: str | None = None
    e2e: tuple[str, ...] = ()
    scenarios: str | None = None


def _value(value: str | tuple[str, ...]) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"


def _tests(draft: Draft) -> str:
    suites = (("unit", draft.unit), ("e2e", draft.e2e))
    return ", ".join(f"{suite} = {_value(roots)}" for suite, roots in suites if roots)


def component_toml(draft: Draft) -> str:
    """Render the `[[components]]` table of a draft."""
    lines = [
        "[[components]]",
        f"id = {_value(draft.id)}",
        'role = "application"',
        f"profile = {_value(draft.profile)}",
        *(
            f"{key} = {_value(value)}"
            for key, value in (
                ("target", draft.target),
                ("framework", draft.framework),
                ("bundler", draft.bundler),
            )
            if value is not None
        ),
        f"root = {_value(draft.root)}",
        f"source = {_value(draft.source)}",
        f"tests = {{ {_tests(draft)} }}",
        f"capabilities = {_value(draft.capabilities)}",
    ]
    if draft.scenarios is not None:
        lines.append(f"scenarios = {_value(draft.scenarios)}")
    if draft.no_boundary_reason is not None:
        lines.append(f"no_boundary_reason = {_value(draft.no_boundary_reason)}")
    return "\n".join(lines) + "\n"


def _components_table(line: str) -> bool | None:
    match = HEADER.match(line)
    if match is None:
        return None
    return match[1] == "components" or match[1].startswith("components.")


def _insertion(lines: list[str]) -> int:
    last = max((i for i, line in enumerate(lines) if _components_table(line)), default=None)
    if last is None:
        return len(lines)
    end = next(
        (i for i in range(last + 1, len(lines)) if _components_table(lines[i]) is False),
        len(lines),
    )
    while end > last + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip()[:1] == "#"):
        end -= 1
    return end


def _joined(head: str, block: str, tail: str) -> str:
    text = head.rstrip("\n") + "\n\n" + block
    return text + "\n" + tail.lstrip("\n") if tail else text


def _parsed(text: str) -> Policy | tuple[Diagnostic, ...]:
    loaded = parse_policy(text.encode("utf-8"))
    return loaded.policy if loaded.policy is not None else loaded.diagnostics


def _extends(old: Policy, new: Policy) -> bool:
    same = new.model_copy(update={"components": old.components}) == old
    return same and new.components[:-1] == old.components


def add_component(text: str, block: str) -> tuple[str, Policy]:
    """Return `text` with the component table `block` added, and the policy it parses to."""
    old = _parsed(text)
    if isinstance(old, tuple):
        raise EditError(old)
    lines = text.splitlines(keepends=True)
    at = _insertion(lines)
    candidates = (
        _joined("".join(lines[:at]), block, "".join(lines[at:])),
        _joined(text, block, ""),
    )
    message = "adding the component would change other policy values"
    problems = (POLICY_INVALID.diagnostic(message, location=Location(POLICY_FILE)),)
    for edited in candidates:
        new = _parsed(edited)
        if isinstance(new, Policy) and _extends(old, new):
            return edited, new
        if isinstance(new, tuple):
            problems = new
    raise EditError(problems)
