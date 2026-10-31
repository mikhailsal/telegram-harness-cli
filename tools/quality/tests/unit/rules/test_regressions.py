import json
from pathlib import Path

import pytest

from quality.coverage import Counter
from quality.diagnostics import Severity
from quality.rules.coverage import ComponentCoverage
from quality.rules.regressions import baseline_regressions, read_baseline, regressions

NOW = (
    ComponentCoverage("app", Counter(8, 10), Counter(1, 2), Counter(0, 0)),
    ComponentCoverage("lib", Counter(3, 3), None, Counter(0, 0)),
    ComponentCoverage("new", Counter(1, 2), Counter(0, 0), Counter(0, 0)),
)


def entry(component: str, line: tuple[int, int] | None, branch: tuple[int, int] | None) -> object:
    def counted(pair: tuple[int, int] | None) -> object:
        return None if pair is None else {"covered": pair[0], "total": pair[1]}

    return {"component": component, "line": counted(line), "branch": counted(branch)}


def write(path: Path, *entries: object) -> Path:
    path.write_text(json.dumps({"coverage": list(entries)}))
    return path


def test_a_drop_below_the_baseline_is_a_warning(tmp_path: Path) -> None:
    baseline = write(
        tmp_path / "report.json",
        entry("app", (9, 10), (1, 2)),
        entry("lib", (2, 3), (1, 1)),
        entry("gone", (1, 1), None),
    )
    found = baseline_regressions(baseline, NOW)
    assert [(d.rule, d.severity, d.component, d.message) for d in found] == [
        (
            "coverage.regression",
            Severity.WARNING,
            "app",
            "line coverage of 'app' fell from 90.00% (9/10) to 80.00% (8/10)",
        )
    ]


def test_equal_ratios_and_measures_without_totals_are_not_regressions() -> None:
    baseline = {
        "app": {"line": Counter(16, 20), "branch": Counter(0, 0)},
        "new": {"branch": Counter(1, 1)},
    }
    assert regressions(baseline, NOW) == []


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"coverage": {}}', '{"coverage": [{"line": null}]}'],
)
def test_an_unreadable_baseline_is_reported_and_changes_nothing(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "report.json"
    path.write_text(content)
    (found,) = baseline_regressions(path, NOW)
    assert (found.rule, found.severity) == ("coverage.baseline", Severity.WARNING)
    assert found.message.startswith("no regressions reported: ")


def test_invalid_counters_are_ignored(tmp_path: Path) -> None:
    bad = {"component": "app", "line": {"covered": 3, "total": 2}, "branch": "x"}
    assert read_baseline(write(tmp_path / "report.json", bad)) == {"app": {}}
