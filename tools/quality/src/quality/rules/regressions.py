"""Coverage regressions against an earlier report: reported, never a ratchet (R06).

The floors in quality.toml decide pass or fail. A baseline, such as the report
of the base branch, only shows where unit coverage fell; a regression is a
warning, so no percentage reached once becomes a new floor.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from quality.boundedio import JsonValue, ReportError, load_json
from quality.coverage import Counter
from quality.diagnostics import Diagnostic, Rule, Severity
from quality.rules.coverage import ComponentCoverage

REGRESSION = Rule(
    id="coverage.regression",
    title="Unit coverage fell below the baseline",
    requirement="R06",
    explanation=(
        "Compared with the baseline report, the component's unit line or branch coverage "
        "is lower. This is reported, not enforced: the floors in quality.toml decide the "
        "verdict, and there is no ratchet."
    ),
    remedy="Check that the drop is intended, for example deleted tests or new untested code.",
    severity=Severity.WARNING,
)
BASELINE = Rule(
    id="coverage.baseline",
    title="The coverage baseline could not be read",
    requirement="R06",
    explanation=(
        "A baseline is an earlier JSON report of `quality gate` or `quality check`. When it "
        "is missing or malformed, regressions are not reported; the verdict is unchanged."
    ),
    remedy="Pass the report.json of an earlier gate run, or omit --baseline.",
    severity=Severity.WARNING,
)
RULES = (REGRESSION, BASELINE)
MEASURES = ("line", "branch")

type Baseline = Mapping[str, Mapping[str, Counter]]


def _counter(value: JsonValue) -> Counter | None:
    match value:
        case {"covered": int(covered), "total": int(total)} if 0 <= covered <= total:
            return Counter(covered, total)
        case _:
            return None


def _entry(entry: JsonValue) -> tuple[str, dict[str, Counter]]:
    if not isinstance(entry, dict) or not isinstance(entry.get("component"), str):
        msg = "a coverage entry needs a component"
        raise ReportError(msg)
    counters = {measure: _counter(entry.get(measure)) for measure in MEASURES}
    return str(entry["component"]), {k: v for k, v in counters.items() if v is not None}


def read_baseline(path: Path) -> Baseline:
    """Read the unit coverage counters of each component from an earlier report."""
    document = load_json(path)
    entries = document.get("coverage") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        msg = f"{path.name} has no coverage list"
        raise ReportError(msg)
    return dict(_entry(entry) for entry in entries)


def _fell(before: Counter, after: Counter) -> bool:
    """Whether after/before decreased, compared exactly on integers."""
    applicable = before.applicable and after.applicable
    return applicable and after.covered * before.total < before.covered * after.total


def regressions(baseline: Baseline, current: Iterable[ComponentCoverage]) -> list[Diagnostic]:
    """Report each component measure whose coverage is lower than in the baseline."""
    found: list[Diagnostic] = []
    for summary in current:
        before = baseline.get(summary.component, {})
        for measure, after in (("line", summary.line), ("branch", summary.branch)):
            earlier = before.get(measure)
            if earlier is not None and after is not None and _fell(earlier, after):
                message = (
                    f"{measure} coverage of '{summary.component}' fell from "
                    f"{earlier.describe()} to {after.describe()}"
                )
                found.append(REGRESSION.diagnostic(message, component=summary.component))
    return found


def baseline_regressions(path: Path, current: Iterable[ComponentCoverage]) -> list[Diagnostic]:
    """Compare with the baseline at `path`; an unreadable baseline is a warning."""
    try:
        baseline = read_baseline(path)
    except ReportError as error:
        return [BASELINE.diagnostic(f"no regressions reported: {error}")]
    return regressions(baseline, current)
