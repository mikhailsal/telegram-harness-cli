import json
import os
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.target import Target
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.stylelint import (
    BASELINE,
    STYLES,
    own_config,
    stylelint_styles,
    unsupported_variants,
)
from quality.boundedio import JsonValue
from quality.diagnostics import Location
from quality.testkit.nodefakes import browser_component, script

WEB = browser_component()
STYLES_FILES = ("web/src/App.css", "web/src/theme.module.css")


def fake(tmp_path: Path, report: JsonValue | None, code: int = 0) -> Path:
    """Write the arguments, then copy the canned report to the `--output-file`."""
    canned = tmp_path / "canned.json"
    if report is not None:
        canned.write_text(json.dumps(report))
    body = (
        f"echo \"$@\" > '{tmp_path / 'argv'}'\n"
        'while [ "$1" != "--output-file" ]; do shift; done\n'
        f"[ -f '{canned}' ] && cp '{canned}' \"$2\"\n"
        f"exit {code}\n"
    )
    return script(tmp_path / "stylelint.sh", body)


def lint(
    node: Node, snapshot: Path, tmp_path: Path, report: JsonValue | None, code: int = 0
) -> list[tuple[Location | None, str]]:
    (snapshot / "web" / "src").mkdir(parents=True, exist_ok=True)
    stylelint = fake(tmp_path, report, code)
    target = Target(WEB, snapshot, STYLES_FILES)
    found = stylelint_styles(target, node=node, script=stylelint, work=tmp_path / "work")
    assert {d.rule for d in found} <= {STYLES.id}
    return [(d.location, d.message) for d in found]


def result(name: str, **fields: JsonValue) -> dict[str, JsonValue]:
    return {"source": name, "warnings": [], **fields}


def test_clean_stylesheets_pass_with_the_baseline(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    report: JsonValue = [result("src/App.css"), result("src/theme.module.css")]
    assert lint(node, snapshot, tmp_path, report) == []
    wrapper = tmp_path / "work" / "stylelint" / "stylelint.json"
    assert json.loads(wrapper.read_text()) == {"extends": list(BASELINE)}
    argv = (tmp_path / "argv").read_text().split()
    assert argv[:4] == ["--config", str(wrapper), "--config-basedir", str(snapshot / "web")]
    assert argv[4:10] == [
        "--formatter",
        "json",
        "--report-needless-disables",
        "--report-invalid-scope-disables",
        "--ignore-path",
        os.devnull,
    ]
    assert argv[-2:] == [str(snapshot / path) for path in STYLES_FILES]


def test_the_components_own_configuration_comes_last(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    directory = snapshot / "web"
    directory.mkdir()
    (directory / "stylelint.config.js").write_text("export default {};\n")
    (directory / ".stylelintrc.json").write_text("{}\n")
    assert own_config(directory) == directory / ".stylelintrc.json"
    lint(node, snapshot, tmp_path, [])
    wrapper = tmp_path / "work" / "stylelint" / "stylelint.json"
    expected = [*BASELINE, str(directory / ".stylelintrc.json")]
    assert json.loads(wrapper.read_text()) == {"extends": expected}
    assert own_config(tmp_path) is None


def test_warnings_parse_errors_and_ignored_files_are_findings(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    warning: JsonValue = {"line": 3, "column": 5, "text": " Named color (color-named) "}
    parse: JsonValue = {"line": 9, "column": 1, "text": "Unclosed block"}
    empty: JsonValue = {"text": ""}
    report: JsonValue = [
        result("src/App.css", warnings=[warning, empty], parseErrors=[parse]),
        result("src/theme.module.css", ignored=True),
    ]
    assert lint(node, snapshot, tmp_path, report, code=2) == [
        (Location("web/src/App.css", 3, 5), "Named color (color-named)"),
        (Location("web/src/App.css"), "Stylelint reported a problem"),
        (Location("web/src/App.css", 9, 1), "Unclosed block"),
        (
            Location("web/src/theme.module.css"),
            "the Stylelint configuration ignores this file; every stylesheet is checked",
        ),
    ]


def test_invalid_options_are_reported_once(node: Node, snapshot: Path, tmp_path: Path) -> None:
    invalid: JsonValue = [{"text": 'Invalid option value "x" for rule "color-named"'}]
    report: JsonValue = [
        result("src/App.css", invalidOptionWarnings=invalid),
        result("src/theme.module.css", invalidOptionWarnings=invalid),
    ]
    assert lint(node, snapshot, tmp_path, report, code=2) == [
        (None, 'invalid Stylelint option: Invalid option value "x" for rule "color-named"')
    ]


def test_a_stylelint_field_in_the_manifest_is_a_finding(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    directory = snapshot / "web"
    directory.mkdir()
    (directory / "package.json").write_text('{"stylelint": {"rules": {}}}')
    message = "the stylelint field of package.json is not read; move it to .stylelintrc.json"
    assert lint(node, snapshot, tmp_path, []) == [(Location("web/package.json"), message)]
    (directory / "package.json").write_text("[]")
    assert lint(node, snapshot, tmp_path, []) == []


@pytest.mark.parametrize(("report", "code"), [(None, 0), ({"source": "x"}, 2), ([], 78)])
def test_a_failed_run_fails_the_tool(
    node: Node, snapshot: Path, tmp_path: Path, report: JsonValue | None, code: int
) -> None:
    with pytest.raises(ToolFailedError, match="stylelint"):
        lint(node, snapshot, tmp_path, report, code)


def test_preprocessor_files_are_unsupported() -> None:
    found = unsupported_variants(WEB, ("web/src/a.scss", "web/src/b.LESS", "web/src/c.styl"))
    assert [(d.rule, d.location, d.message.split(" is ")[0]) for d in found] == [
        (STYLES.id, Location("web/src/a.scss"), "Sass"),
        (STYLES.id, Location("web/src/b.LESS"), "Less"),
        (STYLES.id, Location("web/src/c.styl"), "Stylus"),
    ]
    assert found[0].message.endswith("in a browser component; write CSS or CSS Modules")
