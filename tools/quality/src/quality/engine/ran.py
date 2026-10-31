"""Where each rule ran over a candidate, so unused exceptions are judged reliably."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from quality.engine.common import COMMON_MEASURED, COMMON_RULES, MEASURED_RULES, TOOL_RULES
from quality.engine.profiles import ProfileAdapter
from quality.policy import Component, Profile
from quality.rules import exceptions


def _own_rules(profile: Profile, adapter: ProfileAdapter | None) -> frozenset[str]:
    """Return the rules that run over a component's files only when it is checked."""
    rules = adapter.rules if adapter is not None else ()
    measured = () if profile in COMMON_MEASURED else MEASURED_RULES
    return frozenset(rule.id for rule in (*rules, *measured))


def _everywhere(active: Iterable[Component], *, common: bool) -> set[str]:
    """Return the rules of the gates over the whole candidate, when they ran."""
    if not common:
        return set()
    ran = {rule.id for rule in (*COMMON_RULES, *TOOL_RULES)}
    if any(component.profile in COMMON_MEASURED for component in active):
        ran.update(rule.id for rule in MEASURED_RULES)
    return ran


def ran_rules(
    active: Iterable[Component],
    checked: Iterable[Component],
    profiles: Mapping[Profile, ProfileAdapter],
    *,
    common: bool = True,
) -> exceptions.Ran:
    """Return the rules that ran and where.

    The common gates run everywhere, and they measure every Python component. A
    component's adapter rules, and the structure rules of profiles whose adapter
    measures, ran only over the files of components that were checked. Without
    the common gates, as in the browser lane, nothing ran over other files.
    """
    active = tuple(active)
    done = {component.id for component in checked}
    own = {c.id: _own_rules(c.profile, profiles.get(c.profile)) for c in active}
    ran = _everywhere(active, common=common).union(
        *(rules for name, rules in own.items() if name in done)
    )
    everything = frozenset(ran)
    unchecked = own if common else dict.fromkeys(own, everything)
    skipped: dict[str, frozenset[str]] = {
        c.root: frozenset() if c.id in done else unchecked[c.id] for c in active
    }
    if not common:
        skipped.setdefault(".", everything)
    return exceptions.Ran(everything, skipped)
