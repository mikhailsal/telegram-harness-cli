from typing import cast

import pytest
import yaml

from quality.ci.gitlab import check_gitlab_lanes
from quality.ci.workflow import Table

PATH = ".gitlab-ci.yml"
WRAPPER = """\
variables:
  QUALITY_LANE: wrong-global-value
  OTHER: keep
.gate:
  script:
    - ./quality gate "$@" --lane "$QUALITY_LANE" --reports reports
  variables:
    QUALITY_LANE: main
.browser:
  extends: .gate
  variables:
    QUALITY_LANE: browser
gate:
  extends: .gate
browser:
  extends: .browser
"""


def findings(text: str) -> list[tuple[str, str]]:
    document = cast(Table, yaml.safe_load(text))
    return [(d.rule, str(d.data["symbol"])) for d in check_gitlab_lanes(PATH, document)]


def test_inherited_scripts_and_job_variables_override_global_and_parent_values() -> None:
    assert findings(WRAPPER) == []


@pytest.mark.parametrize(
    "replacement",
    ["", "browser:\n  extends: .browser\n  variables:\n    QUALITY_LANE: main\n"],
)
def test_removing_or_relabelling_the_browser_job_reports_its_missing_lane(replacement: str) -> None:
    assert findings(WRAPPER.replace("browser:\n  extends: .browser\n", replacement)) == [
        ("ci.lanes", "browser")
    ]


@pytest.mark.parametrize("value", ["$MISSING", "${MISSING}", "wrong", "''", "null", "true"])
def test_unresolved_empty_unknown_or_nonstring_lane_variables_fail_closed(value: str) -> None:
    text = WRAPPER.replace("QUALITY_LANE: browser", f"QUALITY_LANE: {value}")
    found = findings(text)
    assert found
    assert {rule for rule, _ in found} == {"ci.lanes"}
    assert ("ci.lanes", "browser") in found


def test_global_variables_and_multiple_parents_are_resolved_in_order() -> None:
    text = """\
variables: {MAIN: main, BROWSER: browser}
.base:
  script: ./quality gate --lane "${QUALITY_LANE}"
  variables: {QUALITY_LANE: $MAIN}
.browser:
  variables: {QUALITY_LANE: $BROWSER}
gate:
  extends: .base
browser:
  extends: [.base, .browser]
"""
    assert findings(text) == []


@pytest.mark.parametrize("parent", [".missing", "browser", "9", "null"])
def test_missing_or_cyclic_extends_fails_closed(parent: str) -> None:
    assert ("ci.lanes", "browser") in findings(
        WRAPPER.replace("browser:\n  extends: .browser", f"browser:\n  extends: {parent}")
    )


def test_variable_cycles_fail_closed() -> None:
    text = WRAPPER.replace(
        "QUALITY_LANE: browser", "QUALITY_LANE: $CYCLE\n    CYCLE: $QUALITY_LANE"
    )
    assert ("ci.lanes", "browser") in findings(text)


@pytest.mark.parametrize(
    "setting",
    ["allow_failure: true", "when: manual", "rules: []", "only: [main]", "except: [main]"],
)
def test_optional_or_conditional_jobs_do_not_supply_a_required_lane(setting: str) -> None:
    text = WRAPPER.replace(
        "browser:\n  extends: .browser", f"browser:\n  {setting}\n  extends: .browser"
    )
    assert ("ci.lanes", "browser") in findings(text)


def test_literal_full_gate_and_nested_script_arrays_are_supported() -> None:
    assert findings("gate:\n  script: [[./quality audit], [./quality gate]]\n") == []


def test_hidden_gate_definitions_require_a_runnable_gate_job() -> None:
    text = ".template:\n  script: ./quality gate --lane browser\nvariables: {LANE: browser}\n"
    assert findings(text) == [("ci.lanes", "gate")]


@pytest.mark.parametrize("settings", ["allow_failure: true", "when: manual", "rules: []"])
def test_all_gate_jobs_optional_fails_even_when_an_audit_job_is_required(settings: str) -> None:
    text = WRAPPER.replace(".gate:\n", f".gate:\n  {settings}\n")
    text += "audit:\n  script: ./quality audit --rev HEAD\n"
    assert findings(text) == [("ci.lanes", "gate")]


def test_an_unconditional_full_gate_can_cover_optional_lane_jobs() -> None:
    text = WRAPPER.replace(".gate:\n", ".gate:\n  allow_failure: true\n")
    text += "complete:\n  script: ./quality gate\n"
    assert findings(text) == []


@pytest.mark.parametrize("variable", ["script", "extends"])
def test_global_variables_named_like_job_settings_cannot_supply_a_gate(variable: str) -> None:
    text = (
        f"variables: {{{variable}: './quality gate'}}\n"
        "gate:\n  script: ./quality gate --lane main\n"
    )
    assert findings(text) == [("ci.lanes", "browser")]


def test_removing_both_visible_lane_jobs_cannot_leave_only_hidden_templates_and_audit() -> None:
    text = WRAPPER.replace("gate:\n  extends: .gate\n", "").replace(
        "browser:\n  extends: .browser\n", ""
    )
    text += "audit:\n  script: ./quality audit\nrequired:\n  script: echo success\n"
    assert findings(text) == [("ci.lanes", "gate")]


def test_unrelated_gitlab_templates_without_a_quality_gate_are_accepted() -> None:
    assert findings(".template:\n  script: echo build\naudit:\n  script: ./quality audit\n") == []
