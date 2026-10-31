from pathlib import Path

import pytest

from quality.ci import ci_gates
from quality.ci.gitlab import REFERENCE
from quality.policy import Category
from quality.rules.model import CandidateFile
from quality.testkit.candidates import candidate, pinned_tools
from quality.testkit.nodefakes import script
from quality.testkit.policies import ComponentSpec, build_policy

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
PATH = ".github/workflows/quality.yml"
WORKFLOW = f"""\
on:
  pull_request:
  merge_group:
permissions: {{}}
jobs:
  gate:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@{SHA}
        with:
          persist-credentials: false
      - run: ./quality gate --stage merge
  required:
    if: always()
    needs: [gate]
    runs-on: ubuntu-24.04
    permissions: {{}}
    steps:
      - env:
          NEEDS: ${{{{ toJSON(needs) }}}}
        run: ./quality ci verdict --needs needs.json
"""
CI = '[ci]\nworkflow = ".github/workflows/quality.yml"\nrequired_jobs = ["gate"]\n'
PLAIN = build_policy(ComponentSpec())
REQUIRED = build_policy(ComponentSpec(), extra=CI)


@pytest.fixture(scope="module")
def actionlint() -> Path:
    return pinned_tools().actionlint


def config(path: str, data: bytes | str | None) -> CandidateFile:
    return candidate(path, data, Category.CONFIG, component=None)


def gates(
    tmp_path: Path, files: list[CandidateFile], *, ci: bool, actionlint: Path
) -> list[tuple[str, str]]:
    root, work = tmp_path / "root", tmp_path / "work"
    work.mkdir(exist_ok=True)
    for file in files:
        if file.data is not None:
            (root / file.path).parent.mkdir(parents=True, exist_ok=True)
            (root / file.path).write_bytes(file.data)
    policy = REQUIRED if ci else PLAIN
    found = ci_gates(files, policy, root, work, actionlint=actionlint)
    return [(d.rule, d.message) for d in found]


def test_a_trusted_workflow_without_ci_policy_is_clean(tmp_path: Path, actionlint: Path) -> None:
    files = [config(PATH, WORKFLOW), candidate("src/app.py", "x = 1\n")]
    assert gates(tmp_path, files, ci=False, actionlint=actionlint) == []


def test_workflow_files_that_cannot_be_read_are_reported(tmp_path: Path, actionlint: Path) -> None:
    files = [config(PATH, None), config(".github/workflows/list.yml", "- a\n")]
    found = gates(tmp_path, files, ci=False, actionlint=actionlint)
    assert found[:2] == [
        ("ci.workflow-syntax", "the workflow is not a regular file within the blob size limit"),
        ("ci.workflow-syntax", ".github/workflows/list.yml does not hold a YAML mapping"),
    ]
    assert {rule for rule, _ in found[2:]} == {"ci.actionlint"}


def test_workflows_are_checked_by_the_trust_rules_and_actionlint(
    tmp_path: Path, actionlint: Path
) -> None:
    text = WORKFLOW.replace("./quality gate", "echo ${{ github.foo }}").replace(SHA, "v7")
    found = gates(tmp_path, [config(PATH, text)], ci=False, actionlint=actionlint)
    assert [rule for rule, _ in found] == ["ci.unpinned-action", "ci.actionlint"]


def test_an_actionlint_failure_is_reported(tmp_path: Path) -> None:
    broken = script(tmp_path / "actionlint", "exit 3\n")
    found = gates(tmp_path, [config(PATH, WORKFLOW)], ci=False, actionlint=broken)
    assert found == [("tools.failed", "actionlint: exit status 3:")]


def test_the_ci_policy_requires_its_workflow_and_codeowners(
    tmp_path: Path, actionlint: Path
) -> None:
    assert gates(tmp_path, [], ci=True, actionlint=actionlint) == [
        ("ci.aggregate", f"the required workflow '{PATH}' is missing or unreadable"),
        ("ci.codeowners", "the CODEOWNERS file '.github/CODEOWNERS' does not exist"),
    ]


def test_every_control_file_needs_an_owner(tmp_path: Path, actionlint: Path) -> None:
    owners = "/.github/ @owner\nquality.toml @-bad\n"
    files = [
        config(PATH, WORKFLOW),
        config(".github/CODEOWNERS", owners),
        config("quality.toml", "schema_version = 1\n"),
        candidate("src/app.py", "x = 1\n"),
    ]
    assert gates(tmp_path, files, ci=True, actionlint=actionlint) == [
        ("ci.codeowners", "'@-bad' is not a user, team, or email address"),
        ("ci.codeowners", "control file 'quality.toml' has no owner in .github/CODEOWNERS"),
    ]


def test_an_unreadable_codeowners_file_is_reported(tmp_path: Path, actionlint: Path) -> None:
    files = [config(PATH, WORKFLOW), config(".github/CODEOWNERS", b"\xff @owner\n")]
    assert gates(tmp_path, files, ci=True, actionlint=actionlint) == [
        (
            "ci.codeowners",
            ".github/CODEOWNERS is not a UTF-8 regular file within the blob size limit",
        )
    ]


@pytest.mark.parametrize("path", [".gitlab-ci.yml", "ci/gitlab/quality.gitlab-ci.yml"])
def test_gitlab_lanes_are_checked_without_a_ci_table(
    tmp_path: Path, actionlint: Path, path: str
) -> None:
    text = """\
.gate:
  script: ./quality gate --lane "$QUALITY_LANE"
gate:
  extends: .gate
  variables: {QUALITY_LANE: main}
browser:
  extends: .gate
  variables: {QUALITY_LANE: browser}
"""
    assert gates(tmp_path, [config(path, text)], ci=False, actionlint=actionlint) == []
    missing = text[: text.index("browser:\n")]
    assert gates(tmp_path, [config(path, missing)], ci=False, actionlint=actionlint) == [
        ("ci.lanes", "the required jobs run the gate's lane main but not 'browser'")
    ]


def test_unreadable_gitlab_yaml_is_reported(tmp_path: Path, actionlint: Path) -> None:
    assert gates(tmp_path, [config(".gitlab-ci.yml", None)], ci=False, actionlint=actionlint) == [
        ("ci.workflow-syntax", "the workflow is not a regular file within the blob size limit")
    ]


def test_the_root_local_include_is_checked_after_its_variables_override_the_wrapper(
    tmp_path: Path, actionlint: Path
) -> None:
    wrapper = """\
.gate:
  script: ./quality gate --lane "$QUALITY_LANE"
gate:
  extends: .gate
  variables: {QUALITY_LANE: main}
browser:
  extends: .gate
  variables: {QUALITY_LANE: browser}
"""
    root = f"include: {{local: {REFERENCE}}}\n"
    files = [config(".gitlab-ci.yml", root), config(REFERENCE, wrapper)]
    assert gates(tmp_path, files, ci=False, actionlint=actionlint) == []
    files[0] = config(".gitlab-ci.yml", root + "browser: {variables: {QUALITY_LANE: main}}\n")
    assert gates(tmp_path, files, ci=False, actionlint=actionlint) == [
        ("ci.lanes", "the required jobs run the gate's lane main but not 'browser'")
    ]


@pytest.mark.parametrize("data", [None, "- not-a-mapping\n"])
def test_an_unreadable_known_local_include_cannot_use_a_standalone_fragment_as_proof(
    tmp_path: Path, actionlint: Path, data: str | None
) -> None:
    files = [config(".gitlab-ci.yml", f"include: {REFERENCE}\n"), config(REFERENCE, data)]
    found = gates(tmp_path, files, ci=False, actionlint=actionlint)
    assert [rule for rule, _ in found] == ["ci.workflow-syntax", "ci.lanes"]
    assert "missing or unreadable" in found[-1][1]


def test_a_missing_known_local_include_is_reported_by_the_common_gate(
    tmp_path: Path, actionlint: Path
) -> None:
    files = [config(".gitlab-ci.yml", f"include: {REFERENCE}\n")]
    assert gates(tmp_path, files, ci=False, actionlint=actionlint) == [
        ("ci.lanes", f"the included GitLab wrapper '{REFERENCE}' is missing or unreadable")
    ]
