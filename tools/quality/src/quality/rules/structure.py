"""Function length, complexity, nesting, and parameter thresholds (R04)."""

from __future__ import annotations

from collections.abc import Iterator

from quality.diagnostics import Diagnostic, Location, Rule
from quality.metrics import FileMetrics, FunctionMetrics, StructureError
from quality.policy import Limit, Thresholds
from quality.rules.files import limit_severity, limit_text

FUNCTION_LINES = Rule(
    id="structure.function-lines",
    title="Function body has too many code lines",
    requirement="R04",
    explanation=(
        "Function and method bodies are measured in nonblank, non-comment code lines with "
        "the counting rules of decision 0007."
    ),
    remedy="Extract cohesive steps into smaller functions.",
)
COMPLEXITY = Rule(
    id="structure.complexity",
    title="Cyclomatic complexity is too high",
    requirement="R04",
    explanation=(
        "Cyclomatic complexity is 1 plus the decision points of the function, as defined "
        "per language in decision 0007. It is not cognitive complexity."
    ),
    remedy="Split decisions into separate functions or replace branches with data.",
)
NESTING = Rule(
    id="structure.nesting",
    title="Control flow is nested too deeply",
    requirement="R04",
    explanation="Nesting counts control-flow blocks inside one function, per decision 0007.",
    remedy="Return early, invert conditions, or extract the inner block.",
)
PARAMETERS = Rule(
    id="structure.parameters",
    title="Function declares too many parameters",
    requirement="R04",
    explanation="Explicit parameters are counted; implicit receivers such as self are not.",
    remedy="Group related parameters into a value object.",
)
UNPARSEABLE = Rule(
    id="structure.unparseable",
    title="Source file cannot be parsed for structure metrics",
    requirement="R04",
    explanation="Structure metrics need a real syntax tree; unparseable code cannot pass.",
    remedy="Fix the syntax error reported by the parser.",
)
RULES = (FUNCTION_LINES, COMPLEXITY, NESTING, PARAMETERS, UNPARSEABLE)


def _measure(
    function: FunctionMetrics, thresholds: Thresholds
) -> Iterator[tuple[Rule, int, Limit, str]]:
    yield FUNCTION_LINES, function.code_lines, thresholds.function_code_lines, "code lines"
    yield COMPLEXITY, function.complexity, thresholds.cyclomatic_complexity, "complexity"
    yield NESTING, function.nesting, thresholds.nesting_depth, "nesting depth"
    yield PARAMETERS, function.parameters, thresholds.parameters, "parameters"


def check_structure(
    metrics: FileMetrics, thresholds: Thresholds, *, component: str | None = None
) -> Iterator[Diagnostic]:
    """Compare every function of one file with the structural thresholds."""
    for function in metrics.functions:
        for rule, value, limit, label in _measure(function, thresholds):
            severity = limit_severity(value, limit)
            if severity is None:
                continue
            yield rule.diagnostic(
                f"'{function.name}' has {label} {value} ({limit_text(limit)})",
                location=Location(metrics.path, function.line),
                component=component,
                severity=severity,
                data={"symbol": function.name},
            )


def unparseable(error: StructureError, *, component: str | None = None) -> Diagnostic:
    """Report a file whose syntax tree cannot be built."""
    return UNPARSEABLE.diagnostic(
        error.message, location=Location(error.path, error.line), component=component
    )
