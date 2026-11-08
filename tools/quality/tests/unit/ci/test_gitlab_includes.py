from typing import cast

import pytest
import yaml

from quality.ci.gitlab import REFERENCE, ROOT, check_gitlab_documents
from quality.ci.workflow import Table

WRAPPER = """\
variables: {MAIN_LANE: main, BROWSER_LANE: browser}
.gate:
  script: ./quality gate --lane "$QUALITY_LANE"
  variables: {QUALITY_LANE: $MAIN_LANE}
.browser:
  extends: .gate
  variables: {QUALITY_LANE: $BROWSER_LANE}
gate:
  extends: .gate
browser:
  extends: .browser
"""


def load(text: str) -> Table:
    return cast(Table, yaml.safe_load(text))


def findings(root: str, reference: str | None = WRAPPER) -> list[tuple[str, str]]:
    documents = {ROOT: load(root), REFERENCE: None if reference is None else load(reference)}
    return [(d.rule, str(d.data["symbol"])) for d in check_gitlab_documents(documents)]


@pytest.mark.parametrize(
    "include",
    [
        f"'{REFERENCE}'",
        f"'/{REFERENCE}'",
        f"'./{REFERENCE}'",
        f"'././{REFERENCE}'",
        "'ci/gitlab/../gitlab/quality.gitlab-ci.yml'",
        f"{{local: '{REFERENCE}'}}",
        f"['{REFERENCE}']",
        f"[{{local: '{REFERENCE}'}}]",
    ],
)
def test_local_string_mapping_and_list_includes_resolve_gate_scripts_and_variables(
    include: str,
) -> None:
    assert findings(f"include: {include}\n") == []


@pytest.mark.parametrize(
    "override",
    [
        "browser: {variables: {QUALITY_LANE: main}}",
        "browser: {extends: .gate}",
        "browser: null",
        "browser: {script: ./quality gate --lane main}",
        "variables: {BROWSER_LANE: main}",
    ],
)
def test_root_overrides_cannot_relabel_or_remove_the_included_browser_lane(override: str) -> None:
    assert findings(f"include: {REFERENCE}\n{override}\n") == [("ci.lanes", "browser")]


def test_root_can_fix_the_included_fragment_without_a_stale_standalone_finding() -> None:
    reference = WRAPPER.replace("QUALITY_LANE: $BROWSER_LANE", "QUALITY_LANE: main")
    root = f"include: {REFERENCE}\nbrowser: {{variables: {{QUALITY_LANE: browser}}}}\n"
    assert findings(root, reference) == []


def test_root_global_variables_and_new_extends_chains_are_resolved_after_merging() -> None:
    root = f"""\
include: {REFERENCE}
variables: {{BROWSER_LANE: $CUSTOM_LANE, CUSTOM_LANE: browser}}
.custom:
  extends: .browser
browser:
  extends: .custom
"""
    assert findings(root) == []


def test_a_missing_or_unreadable_included_wrapper_fails_closed() -> None:
    assert findings(f"include: {REFERENCE}\n", None) == [("ci.lanes", REFERENCE)]
    assert [d.rule for d in check_gitlab_documents({ROOT: load(f"include: {REFERENCE}\n")})] == [
        "ci.lanes"
    ]


@pytest.mark.parametrize(
    "include",
    [
        f"[{{local: '{REFERENCE}', rules: []}}]",
        f"['{REFERENCE}', {{remote: 'https://example.invalid/ci.yml'}}]",
        f"['{REFERENCE}', {{local: other.yml}}]",
        f"['{REFERENCE}', 42]",
    ],
)
def test_known_wrapper_composition_with_unresolved_entries_fails_closed(include: str) -> None:
    assert findings(f"include: {include}\n") == [("ci.lanes", "include")]


def test_nested_includes_in_the_known_wrapper_cannot_supply_unverified_lane_proof() -> None:
    assert findings(f"include: {REFERENCE}\n", "include: other.yml\n" + WRAPPER) == [
        ("ci.lanes", "include")
    ]


@pytest.mark.parametrize("include", ["null", "[]", "{remote: https://example.invalid/ci.yml}"])
def test_unrelated_includes_without_the_known_wrapper_are_accepted(include: str) -> None:
    assert findings(f"include: {include}\naudit: {{script: ./quality audit}}\n", None) == []


@pytest.mark.parametrize("local", [f"././{REFERENCE}", "ci/gitlab/../gitlab/quality.gitlab-ci.yml"])
def test_normalized_known_include_paths_cannot_hide_root_browser_relabelling(local: str) -> None:
    root = f"include: {{local: '{local}'}}\nbrowser: {{variables: {{QUALITY_LANE: main}}}}\n"
    assert findings(root) == [("ci.lanes", "browser")]
