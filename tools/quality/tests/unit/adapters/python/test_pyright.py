import json
from collections.abc import Callable
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.python.pyright import pyright_types
from quality.adapters.target import Target
from quality.diagnostics import Location, Severity
from quality.testkit.policies import ComponentSpec, build_policy

type Fake = Callable[[str, int], Path]

(APP,) = build_policy(ComponentSpec(root="app")).components
FILE = "app/src/pkg/c.py"
CONFIG = "pyrightconfig.json"


def run(component: Path, tools: Path, pyright: Path | None = None) -> list[tuple[object, ...]]:
    target = Target(APP, component.parent, (FILE,))
    found = pyright_types(
        target, pyright=pyright or tools / "pyright", config=CONFIG, python=tools / "python"
    )
    return [(d.rule, d.severity, d.location, d.message, d.component) for d in found]


def settings(component: Path, **extra: object) -> None:
    (component / CONFIG).write_text(json.dumps({"typeCheckingMode": "strict", **extra}))


def test_errors_and_warnings_are_reported(component: Path, tools: Path) -> None:
    settings(component, reportUnusedImport="warning")
    (component / "src/pkg/c.py").write_text('import os\nx: int = "s"\n')
    found = run(component, tools)
    assert [entry[:3] for entry in found] == [
        ("python.types", Severity.WARNING, Location(FILE, 1, 8)),
        ("python.types", Severity.ERROR, Location(FILE, 2, 10)),
    ]
    assert found[0][3:] == ('Import "os" is not accessed (reportUnusedImport)', "app")


def test_clean_files_pass(component: Path, tools: Path) -> None:
    settings(component)
    (component / "src/pkg/c.py").write_text("x: int = 1\n")
    assert run(component, tools) == []


def test_files_kept_out_of_analysis_fail(component: Path, tools: Path) -> None:
    settings(component, exclude=["src"])
    (component / "src/pkg/c.py").write_text('x: int = "s"\n')
    message = "Pyright analyzed 0 of 1 files; do not exclude them"
    assert run(component, tools) == [
        ("python.types", Severity.ERROR, Location("app/pyrightconfig.json"), message, "app")
    ]


def entry(severity: str, **extra: object) -> dict[str, object]:
    return {"file": "src/pkg/c.py", "severity": severity, "message": "m", **extra}


def output(entries: list[dict[str, object]], errors: int, analyzed: object = 1) -> str:
    summary = {"errorCount": errors, "filesAnalyzed": analyzed}
    return json.dumps({"generalDiagnostics": entries, "summary": summary})


def test_report_entries_are_mapped(component: Path, tools: Path, fake: Fake) -> None:
    span = {"start": {"line": 3, "character": 0}}
    entries = [
        entry("information"),
        entry("error", range=span, rule="reportX"),
        entry("warning", range={"start": "x"}),
        entry("warning", range=[]),
        entry("error", message='Type is partially unknown\n  Type of "x" is "int"', rule="reportY"),
    ]
    found = run(component, tools, fake(output(entries, 2), 1))
    assert [(severity, where, message) for _, severity, where, message, _ in found] == [
        (Severity.ERROR, Location(FILE, 4, 1), "m (reportX)"),
        (Severity.WARNING, Location(FILE), "m"),
        (Severity.WARNING, Location(FILE), "m"),
        (
            Severity.ERROR,
            Location(FILE),
            'Type is partially unknown; Type of "x" is "int" (reportY)',
        ),
    ]


@pytest.mark.parametrize(
    ("stdout", "code", "message"),
    [
        (output([entry("error")], 0), 1, "pyright: exit status 1 disagrees with 1 errors"),
        (output([entry("error")], 1), 0, "pyright: exit status 0 disagrees with 1 errors"),
        (output([], 0), 1, "pyright: exit status 1 disagrees with 0 errors"),
        (output([], 0, analyzed="1"), 0, "pyright: summary has no filesAnalyzed count"),
        (json.dumps({"generalDiagnostics": []}), 0, "pyright: output has no summary"),
        ("[]", 0, "pyright: output is not a list of findings"),
        ("Error: no node", 0, "pyright: malformed JSON in pyright output: "),
        (output([], 0), 2, "pyright: exit status 2: fake stderr"),
    ],
)
def test_inconsistent_pyright_output_fails(
    component: Path, tools: Path, fake: Fake, stdout: str, code: int, message: str
) -> None:
    with pytest.raises(ToolFailedError) as raised:
        run(component, tools, fake(stdout, code))
    assert raised.value.diagnostic.message.startswith(message)
