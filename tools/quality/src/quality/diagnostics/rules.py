"""Rule descriptions used by diagnostics and `quality explain`."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from quality.diagnostics.model import Diagnostic, Kind, Location, Severity

RULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")


@dataclass(frozen=True, slots=True)
class Rule:
    """A stable rule identity with its explanation and corrective action."""

    id: str
    title: str
    requirement: str
    explanation: str
    remedy: str
    severity: Severity = Severity.ERROR
    kind: Kind = Kind.VIOLATION

    def __post_init__(self) -> None:
        if not RULE_ID_PATTERN.fullmatch(self.id):
            msg = f"invalid rule id: {self.id!r}"
            raise ValueError(msg)

    def diagnostic(
        self,
        message: str,
        *,
        location: Location | None = None,
        component: str | None = None,
        severity: Severity | None = None,
        data: Mapping[str, str] | None = None,
    ) -> Diagnostic:
        """Create a diagnostic for this rule with the rule's remedy as hint."""
        return Diagnostic(
            rule=self.id,
            severity=severity or self.severity,
            message=message,
            location=location,
            component=component,
            hint=self.remedy,
            kind=self.kind,
            data=MappingProxyType(dict(data or {})),
        )


class RuleRegistry:
    """An immutable set of rules indexed by ID."""

    def __init__(self, rules: Iterable[Rule]) -> None:
        index: dict[str, Rule] = {}
        for rule in rules:
            if rule.id in index:
                msg = f"duplicate rule id: {rule.id}"
                raise ValueError(msg)
            index[rule.id] = rule
        self._rules: Mapping[str, Rule] = MappingProxyType(dict(sorted(index.items())))

    def get(self, rule_id: str) -> Rule | None:
        """Return the rule with this ID, if any."""
        return self._rules.get(rule_id)

    def ids(self) -> frozenset[str]:
        """All registered rule IDs."""
        return frozenset(self._rules)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._rules.values())

    def explain(self, rule_id: str) -> str | None:
        """Render the explanation of one rule, or None if unknown."""
        rule = self.get(rule_id)
        if rule is None:
            return None
        return "\n".join(
            [
                f"{rule.id}: {rule.title}",
                f"Requirement: {rule.requirement}",
                f"Default severity: {rule.severity.value}",
                "",
                rule.explanation,
                "",
                f"How to fix: {rule.remedy}",
            ]
        )
