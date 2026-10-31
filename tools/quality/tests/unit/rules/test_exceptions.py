from datetime import date

import pytest

from quality.diagnostics import Diagnostic, Kind, Location, Rule, Severity
from quality.policy import PolicyException
from quality.rules.exceptions import Applied, Ran, apply_exceptions, describe, exceptable

TODAY = date(2026, 10, 20)
LATER = date(2027, 1, 1)
POLICY = Location("quality.toml")


def rule(rule_id: str, kind: Kind = Kind.VIOLATION) -> Rule:
    return Rule(id=rule_id, title="t", requirement="R05", explanation="e", remedy="r", kind=kind)


RULES = {
    found.id: found
    for found in [
        rule("suppressions.inline"),
        rule("files.blob-size"),
        rule("structure.complexity"),
        rule("tools.failed", Kind.ENVIRONMENT),
        rule("policy.invalid", Kind.USAGE),
        rule("git.symlink-escape"),
        rule("discovery.unclassified"),
        rule("secrets.detected"),
        rule("exceptions.unused"),
    ]
}
RAN = Ran(frozenset(RULES))
NONE = Ran(frozenset())


def exception(
    rule_id: str = "suppressions.inline",
    path: str = "src/a.py",
    symbol: str | None = "noqa",
    *,
    expires: date | None = None,
    review: date | None = LATER,
) -> PolicyException:
    return PolicyException(
        rule=rule_id,
        path=path,
        symbol=symbol,
        reason="needed",
        owner="core team",
        expires=expires,
        review=review,
    )


def finding(
    rule_id: str = "suppressions.inline",
    path: str | None = "src/a.py",
    symbol: str | None = "noqa",
    kind: Kind = Kind.VIOLATION,
) -> Diagnostic:
    data = {"symbol": symbol} if symbol is not None else {}
    location = Location(path, 3) if path is not None else None
    return rule(rule_id, kind).diagnostic("found", location=location, data=data)


def apply(
    diagnostics: list[Diagnostic],
    *exceptions: PolicyException,
    ran: Ran = RAN,
    today: date = TODAY,
) -> Applied:
    return apply_exceptions(diagnostics, exceptions, rules=RULES, ran=ran, today=today)


def summary(applied: Applied) -> list[tuple[str, Severity, str]]:
    return [(d.rule, d.severity, d.message) for d in applied.diagnostics]


def test_an_exact_match_removes_the_finding() -> None:
    applied = apply([finding(), finding(path="src/b.py")], exception())
    assert applied.excepted == 1
    assert [d.location for d in applied.diagnostics] == [Location("src/b.py", 3)]


@pytest.mark.parametrize(
    "diagnostic",
    [
        finding(rule_id="files.blob-size"),
        finding(path="src/a.pyi"),
        finding(path="src"),
        finding(symbol="noqa: E501"),
        finding(symbol=None),
        finding(path=None),
        finding(kind=Kind.ENVIRONMENT),
    ],
    ids=["rule", "path", "directory", "symbol", "no-symbol", "no-location", "kind"],
)
def test_anything_but_an_exact_match_is_kept(diagnostic: Diagnostic) -> None:
    applied = apply([diagnostic], exception(), ran=NONE)
    assert applied.diagnostics == (diagnostic,)
    assert applied.excepted == 0


def test_an_exception_without_symbol_matches_only_findings_without_symbol() -> None:
    plain = exception("files.blob-size", "big.bin", None)
    applied = apply([finding("files.blob-size", "big.bin", None)], plain)
    assert (applied.diagnostics, applied.excepted) == ((), 1)
    applied = apply([finding("files.blob-size", "big.bin", "x")], plain, ran=NONE)
    assert applied.excepted == 0


def test_one_exception_covers_every_matching_finding() -> None:
    applied = apply([finding(), finding()], exception())
    assert (applied.diagnostics, applied.excepted) == ((), 2)


def test_the_first_matching_exception_is_used_and_the_duplicate_is_unused() -> None:
    applied = apply([finding()], exception(), exception())
    assert applied.excepted == 1
    assert summary(applied) == [
        (
            "exceptions.unused",
            Severity.ERROR,
            "exception for 'suppressions.inline' at 'src/a.py' 'noqa' matches no finding",
        )
    ]
    assert applied.diagnostics[0].location == POLICY


def test_unused_exceptions_are_reported_only_for_rules_that_ran() -> None:
    assert summary(apply([], exception(), ran=NONE)) == []
    assert summary(apply([], exception("structure.complexity", "src/a.py", "f"))) == [
        (
            "exceptions.unused",
            Severity.ERROR,
            "exception for 'structure.complexity' at 'src/a.py' 'f' matches no finding",
        )
    ]


def test_unused_exceptions_are_reported_only_where_their_rule_ran() -> None:
    none = frozenset[str]()
    skipped = {".": none, "web": frozenset({"structure.complexity"}), "web/vendor": none}
    ran = Ran(frozenset(RULES), skipped)
    assert not ran.covers("structure.complexity", "web/src/a.ts")
    assert ran.covers("structure.complexity", "web/vendor/a.ts")
    assert ran.covers("structure.complexity", "src/a.py")
    assert ran.covers("structure.complexity", "webapp/a.ts")
    assert ran.covers("suppressions.inline", "web/src/a.ts")
    assert not ran.covers("python.lint", "src/a.py")
    unused = exception("structure.complexity", "web/src/a.ts", "f")
    assert summary(apply([], unused, ran=ran)) == []
    assert summary(apply([], unused, ran=Ran(frozenset(RULES), {".": frozenset()}))) == [
        (
            "exceptions.unused",
            Severity.ERROR,
            "exception for 'structure.complexity' at 'web/src/a.ts' 'f' matches no finding",
        )
    ]


def test_expired_exceptions_fail_and_no_longer_apply() -> None:
    expired = exception(expires=date(2026, 10, 19), review=None)
    applied = apply([finding()], expired)
    assert applied.excepted == 0
    assert summary(applied) == [
        (
            "exceptions.expired",
            Severity.ERROR,
            "exception for 'suppressions.inline' at 'src/a.py' 'noqa' expired on 2026-10-19",
        ),
        ("suppressions.inline", Severity.ERROR, "found"),
    ]
    assert applied.diagnostics[0].location == POLICY


def test_an_exception_expiring_today_still_applies() -> None:
    applied = apply([finding()], exception(expires=TODAY, review=None))
    assert (applied.diagnostics, applied.excepted) == ((), 1)


def test_review_due_exceptions_warn_and_still_apply() -> None:
    due = exception(review=date(2026, 10, 19))
    applied = apply([finding()], due)
    assert applied.excepted == 1
    assert summary(applied) == [
        (
            "exceptions.review-due",
            Severity.WARNING,
            (
                "exception for 'suppressions.inline' at 'src/a.py' 'noqa' was due for review on "
                "2026-10-19"
            ),
        )
    ]
    assert summary(apply([finding()], exception(review=TODAY))) == []


def test_expiry_is_checked_before_review() -> None:
    both = exception(expires=date(2026, 1, 1), review=date(2026, 1, 1))
    assert [d.rule for d in apply([], both).diagnostics] == ["exceptions.expired"]


@pytest.mark.parametrize(
    "rule_id",
    [
        "policy.invalid",
        "git.symlink-escape",
        "discovery.unclassified",
        "secrets.detected",
        "exceptions.unused",
        "tools.failed",
    ],
)
def test_fixed_rules_cannot_be_excepted(rule_id: str) -> None:
    blocked = exception(rule_id, "src/a.py", None)
    applied = apply([finding(rule_id, symbol=None, kind=RULES[rule_id].kind)], blocked)
    assert applied.excepted == 0
    assert [d.rule for d in applied.diagnostics] == ["exceptions.not-allowed", rule_id]
    assert applied.diagnostics[0].message == (
        f"exception for '{rule_id}' at 'src/a.py' is not allowed"
    )


def test_exceptions_for_unknown_rules_apply_and_are_never_reported_unused() -> None:
    unknown = exception("future.rule", "src/a.py", None)
    assert summary(apply([], unknown)) == []
    applied = apply([finding("future.rule", symbol=None)], unknown)
    assert (applied.diagnostics, applied.excepted) == ((), 1)


@pytest.mark.parametrize(
    ("rule_id", "kind", "expected"),
    [
        ("files.blob-size", Kind.VIOLATION, True),
        ("suppressions.inline", Kind.VIOLATION, True),
        ("tools.failed", Kind.ENVIRONMENT, False),
        ("policy.syntax", Kind.USAGE, False),
        ("secrets.detected", Kind.VIOLATION, False),
        ("gitleaks.anything", Kind.VIOLATION, True),
    ],
)
def test_exceptable(rule_id: str, kind: Kind, expected: bool) -> None:
    assert exceptable(rule(rule_id, kind)) is expected


def test_describe_names_the_scope() -> None:
    assert describe(exception()) == "exception for 'suppressions.inline' at 'src/a.py' 'noqa'"
    assert describe(exception(symbol=None)) == "exception for 'suppressions.inline' at 'src/a.py'"
