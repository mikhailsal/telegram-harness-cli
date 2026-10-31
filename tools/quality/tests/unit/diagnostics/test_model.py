from quality.diagnostics import Diagnostic, ExitCode, Kind, Location, Outcome, Severity


def make(
    rule: str = "a.rule",
    *,
    severity: Severity = Severity.ERROR,
    kind: Kind = Kind.VIOLATION,
    location: Location | None = None,
    message: str = "m",
) -> Diagnostic:
    return Diagnostic(rule=rule, severity=severity, message=message, location=location, kind=kind)


def test_location_renders_only_known_parts() -> None:
    assert Location("a.py").render() == "a.py"
    assert Location("a.py", 3).render() == "a.py:3"
    assert Location("a.py", 3, 7).render() == "a.py:3:7"
    assert Location("a.py", None, 7).render() == "a.py"


def test_outcome_orders_by_path_line_rule_message() -> None:
    unsorted = [
        make("b.rule", location=Location("b.py", 1)),
        make("a.rule", location=Location("b.py", 1)),
        make("a.rule", location=Location("a.py", 9)),
        make("z.rule"),
        make("a.rule", location=Location("a.py")),
    ]
    ordered = Outcome.of(unsorted).diagnostics
    assert [(d.location.render() if d.location else "", d.rule) for d in ordered] == [
        ("", "z.rule"),
        ("a.py", "a.rule"),
        ("a.py:9", "a.rule"),
        ("b.py:1", "a.rule"),
        ("b.py:1", "b.rule"),
    ]


def test_outcome_without_errors_passes_even_with_warnings() -> None:
    outcome = Outcome.of([make(severity=Severity.WARNING), make(severity=Severity.INFO)])
    assert outcome.errors == ()
    assert len(outcome.warnings) == 1
    assert outcome.exit_code() is ExitCode.OK


def test_exit_code_priority_is_usage_then_environment_then_violations() -> None:
    violation = make(kind=Kind.VIOLATION)
    environment = make(kind=Kind.ENVIRONMENT)
    usage = make(kind=Kind.USAGE)
    assert Outcome.of([violation]).exit_code() is ExitCode.VIOLATIONS
    assert Outcome.of([violation, environment]).exit_code() is ExitCode.ENVIRONMENT
    assert Outcome.of([violation, environment, usage]).exit_code() is ExitCode.USAGE


def test_non_error_usage_diagnostic_does_not_fail() -> None:
    outcome = Outcome.of([make(kind=Kind.USAGE, severity=Severity.WARNING)])
    assert outcome.exit_code() is ExitCode.OK


def test_diagnostic_data_defaults_to_an_empty_read_only_mapping() -> None:
    assert dict(make().data) == {}
