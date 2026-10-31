import pytest

from quality.diagnostics import Location, Severity
from quality.metrics import FileMetrics, FunctionMetrics, StructureError
from quality.policy import Thresholds
from quality.rules.structure import check_structure, unparseable
from quality.testkit.policies import build_policy

THRESHOLDS: Thresholds = build_policy().thresholds
PATH = "src/app/mod.py"


def function(
    name: str = "f",
    *,
    code_lines: int = 1,
    complexity: int = 1,
    nesting: int = 0,
    parameters: int = 0,
) -> FunctionMetrics:
    return FunctionMetrics(name, 7, code_lines, complexity, nesting, parameters)


def run(*functions: FunctionMetrics) -> list[tuple[str, str, Severity]]:
    metrics = FileMetrics(PATH, functions, ())
    return [(d.rule, d.message, d.severity) for d in check_structure(metrics, THRESHOLDS)]


def test_functions_at_every_limit_pass() -> None:
    at_limits = function(code_lines=50, complexity=10, nesting=3, parameters=5)
    assert run(at_limits) == []


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (
            function(code_lines=51),
            ("structure.function-lines", "'f' has code lines 51 (error above 50)", "error"),
        ),
        (
            function(complexity=11),
            ("structure.complexity", "'f' has complexity 11 (error above 10)", "error"),
        ),
        (
            function(nesting=4),
            (
                "structure.nesting",
                "'f' has nesting depth 4 (warning above 3, error above 4)",
                "warning",
            ),
        ),
        (
            function(nesting=5),
            (
                "structure.nesting",
                "'f' has nesting depth 5 (warning above 3, error above 4)",
                "error",
            ),
        ),
        (
            function(parameters=6),
            ("structure.parameters", "'f' has parameters 6 (warning above 5)", "warning"),
        ),
    ],
)
def test_one_over_each_limit_is_reported(
    metrics: FunctionMetrics, expected: tuple[str, str, str]
) -> None:
    assert [(rule, message, str(severity)) for rule, message, severity in run(metrics)] == [
        expected
    ]


def test_every_violation_of_every_function_is_reported_with_location_and_symbol() -> None:
    worst = function("Box.method", code_lines=60, complexity=12, nesting=5, parameters=9)
    metrics = FileMetrics(PATH, (function("fine"), worst), ())
    found = list(check_structure(metrics, THRESHOLDS, component="app"))
    assert [d.rule for d in found] == [
        "structure.function-lines",
        "structure.complexity",
        "structure.nesting",
        "structure.parameters",
    ]
    assert {d.location for d in found} == {Location(PATH, 7)}
    assert {d.component for d in found} == {"app"}
    assert {d.data["symbol"] for d in found} == {"Box.method"}


def test_component_defaults_to_none() -> None:
    metrics = FileMetrics(PATH, (function(complexity=11),), ())
    assert [d.component for d in check_structure(metrics, THRESHOLDS)] == [None]


def test_unparseable_files_are_errors() -> None:
    error = StructureError(PATH, 3, "invalid syntax")
    found = unparseable(error, component="app")
    assert (found.rule, found.message, found.location, found.component, found.severity) == (
        "structure.unparseable",
        "invalid syntax",
        Location(PATH, 3),
        "app",
        Severity.ERROR,
    )
    assert unparseable(StructureError(PATH, None, "bad")).location == Location(PATH)
