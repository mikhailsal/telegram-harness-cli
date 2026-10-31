import json
from pathlib import Path

import pytest

from quality.adapters.actionlint import lint_workflows
from quality.adapters.native import ToolFailedError
from quality.testkit.candidates import pinned_tools
from quality.testkit.nodefakes import script

CLEAN = "on: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo\n"
BROKEN = CLEAN.replace("echo", "echo ${{ github.foo }}")
PATH = ".github/workflows/a.yml"


@pytest.fixture(scope="module")
def actionlint() -> Path:
    return pinned_tools().actionlint


def tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    work = root.parent / "work"
    work.mkdir(exist_ok=True)
    return work


def test_no_workflows_run_nothing(tmp_path: Path) -> None:
    assert lint_workflows([], tmp_path, tmp_path, actionlint=tmp_path / "absent") == []


def test_a_clean_workflow_has_no_findings(tmp_path: Path, actionlint: Path) -> None:
    root = tmp_path / "root"
    work = tree(root, {PATH: CLEAN})
    assert lint_workflows([PATH], root, work, actionlint=actionlint) == []


def test_findings_keep_the_native_kind_and_position(tmp_path: Path, actionlint: Path) -> None:
    root = tmp_path / "root"
    work = tree(root, {PATH: BROKEN, ".github/workflows/b.yaml": CLEAN})
    [found] = lint_workflows([PATH, ".github/workflows/b.yaml"], root, work, actionlint=actionlint)
    assert found.rule == "ci.actionlint"
    assert found.message.startswith('expression: property "foo" is not defined')
    assert found.data["symbol"] == "expression"
    assert found.location is not None
    assert (found.location.path, found.location.line, found.location.column) == (PATH, 6, 23)


def test_a_candidate_configuration_is_ignored(tmp_path: Path, actionlint: Path) -> None:
    root = tmp_path / "root"
    config = "self-hosted-runner:\n  labels: [mine]\n"
    work = tree(
        root, {PATH: CLEAN.replace("ubuntu-24.04", "mine"), ".github/actionlint.yaml": config}
    )
    [found] = lint_workflows([PATH], root, work, actionlint=actionlint)
    assert found.data["symbol"] == "runner-label"


def fake(tmp_path: Path, status: int, report: object) -> Path:
    body = f"cat <<'EOF'\n{json.dumps(report)}\nEOF\nexit {status}\n"
    return script(tmp_path / "fake-actionlint", body)


ENTRY = {"message": "m", "filepath": PATH, "line": 1, "column": 2, "kind": "syntax-check"}


@pytest.mark.parametrize(
    ("status", "report", "message"),
    [
        (0, [ENTRY], "exit status and report disagree"),
        (1, [], "exit status and report disagree"),
        (1, {"errors": []}, "report is not a list"),
        (1, ["text"], "report entry is not an object"),
        (1, [{**ENTRY, "filepath": "other.yml"}], "finding outside the linted workflows"),
        (1, [{**ENTRY, "line": "1"}], "report entry without number field line"),
        (1, [{**ENTRY, "line": True}], "report entry without number field line"),
        (1, [{**ENTRY, "kind": None}], "report entry without text field kind"),
        (3, [], "exit status 3"),
    ],
)
def test_unusable_reports_fail_the_tool(
    tmp_path: Path, status: int, report: object, message: str
) -> None:
    work = tree(tmp_path / "root", {PATH: CLEAN})
    with pytest.raises(ToolFailedError, match=message):
        lint_workflows([PATH], tmp_path / "root", work, actionlint=fake(tmp_path, status, report))


def test_unparsable_and_truncated_reports_fail_the_tool(tmp_path: Path) -> None:
    work = tree(tmp_path / "root", {PATH: CLEAN})
    broken = script(tmp_path / "broken", "echo '[{'\nexit 1\n")
    with pytest.raises(ToolFailedError, match="actionlint output"):
        lint_workflows([PATH], tmp_path / "root", work, actionlint=broken)
    flood = script(tmp_path / "flood", "head -c 17000000 /dev/zero\nexit 1\n")
    with pytest.raises(ToolFailedError, match="output limit"):
        lint_workflows([PATH], tmp_path / "root", work, actionlint=flood)
