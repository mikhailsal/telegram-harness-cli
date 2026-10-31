from pathlib import Path

import pytest

from quality.hooks import HookName, HookState, HookStatus, status_diagnostics

REQUIRED = frozenset({HookName.PRE_COMMIT})
HOOKS = Path("/repo/.git/hooks")


def findings(name: HookName, status: HookStatus) -> list[tuple[str, str, str]]:
    state = HookState(name, HOOKS / str(name), status)
    return [
        (found.rule, found.severity, found.message)
        for found in status_diagnostics([state], required=REQUIRED)
    ]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HookStatus.CURRENT, []),
        (HookStatus.MISSING, [("hooks.missing", "error", "is missing")]),
        (HookStatus.OUTDATED, [("hooks.missing", "error", "is outdated")]),
        (HookStatus.FOREIGN, [("hooks.conflict", "error", "is not managed by quality")]),
    ],
)
def test_the_required_hook_must_be_current(
    status: HookStatus, expected: list[tuple[str, str, str]]
) -> None:
    where = f"the pre-commit hook at {HOOKS / 'pre-commit'} "
    assert findings(HookName.PRE_COMMIT, status) == [
        (rule, severity, where + text) for rule, severity, text in expected
    ]


@pytest.mark.parametrize("status", [HookStatus.CURRENT, HookStatus.MISSING, HookStatus.FOREIGN])
def test_an_optional_hook_may_be_absent_or_the_users_own(status: HookStatus) -> None:
    assert findings(HookName.PRE_PUSH, status) == []


def test_an_outdated_optional_hook_warns() -> None:
    message = f"the pre-push hook at {HOOKS / 'pre-push'} is outdated"
    assert findings(HookName.PRE_PUSH, HookStatus.OUTDATED) == [
        ("hooks.missing", "warning", message)
    ]


def test_findings_follow_the_order_of_the_states() -> None:
    states = [
        HookState(HookName.PRE_PUSH, HOOKS / "pre-push", HookStatus.OUTDATED),
        HookState(HookName.PRE_COMMIT, HOOKS / "pre-commit", HookStatus.MISSING),
    ]
    found = status_diagnostics(states, required=REQUIRED)
    assert [(item.rule, item.severity, item.kind) for item in found] == [
        ("hooks.missing", "warning", "environment"),
        ("hooks.missing", "error", "environment"),
    ]
    assert all(
        item.hint == "Run './quality bootstrap' to install the current hooks." for item in found
    )
