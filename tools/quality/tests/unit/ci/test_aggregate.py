from collections.abc import Mapping
from typing import cast

import pytest
import yaml

from quality.ci.aggregate import check_required
from quality.policy import CiPolicy

PATH = ".github/workflows/quality.yml"
POLICY = CiPolicy(workflow=PATH, required_jobs=["gate", "audit"])

WORKFLOW = """\
on:
  pull_request:
  merge_group:
  push:
    branches: [main]
permissions: {}
jobs:
  gate:
    runs-on: ubuntu-24.04
    steps:
      - run: ./quality gate
  audit:
    runs-on: ubuntu-24.04
    steps:
      - run: ./quality audit
  required:
    if: always()
    needs: [gate, audit]
    runs-on: ubuntu-24.04
    steps:
      - env:
          NEEDS: ${{ toJSON(needs) }}
        run: |
          printf '%s' "$NEEDS" > needs.json
          ./quality ci verdict --needs needs.json
"""


def load(text: str) -> Mapping[str, object]:
    document = yaml.safe_load(text)
    assert isinstance(document, dict)
    return cast(Mapping[str, object], document)


def found(text: str, policy: CiPolicy = POLICY) -> list[tuple[str, str, str]]:
    return [(d.rule, d.message, str(d.data["symbol"])) for d in check_required(load(text), policy)]


def test_an_always_running_aggregate_over_the_required_jobs_is_clean() -> None:
    assert found(WORKFLOW) == []


def test_the_aggregate_may_use_the_expression_form_and_a_job_level_env() -> None:
    aggregate = """\
  required:
    if: ${{ always() }}
    needs: [audit, gate]
    env:
      NEEDS: ${{ toJSON(needs) }}
    runs-on: ubuntu-24.04
    steps:
      - run: ./quality ci verdict --needs needs.json
"""
    assert found(WORKFLOW[: WORKFLOW.index("  required:")] + aggregate) == []


def test_a_missing_workflow_is_reported_at_its_configured_path() -> None:
    [diagnostic] = check_required(None, POLICY)
    assert diagnostic.message == f"the required workflow '{PATH}' is missing or unreadable"
    assert diagnostic.location is not None
    assert diagnostic.location.path == PATH


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
def test_the_required_workflow_runs_on_pull_requests_and_the_merge_queue(event: str) -> None:
    text = WORKFLOW.replace(f"  {event}:\n", "")
    assert found(text) == [
        ("ci.aggregate", f"the required workflow does not run on '{event}'", event)
    ]


@pytest.mark.parametrize("key", ["paths", "paths-ignore"])
def test_path_filters_on_any_trigger_are_reported(key: str) -> None:
    text = WORKFLOW.replace("    branches: [main]\n", f"    branches: [main]\n    {key}: [src]\n")
    assert found(text) == [
        ("ci.path-filter", f"the trigger 'push' is filtered by '{key}'", f"push:{key}")
    ]


def test_a_missing_aggregate_job_is_reported() -> None:
    policy = CiPolicy(workflow=PATH, aggregate_job="all-green", required_jobs=["gate", "audit"])
    assert found(WORKFLOW, policy) == [
        ("ci.aggregate", "the aggregate job 'all-green' does not exist", "all-green")
    ]


@pytest.mark.parametrize("condition", ["    if: success()\n", ""])
def test_an_aggregate_that_does_not_always_run_is_reported(condition: str) -> None:
    text = WORKFLOW.replace("    if: always()\n", condition)
    assert found(text) == [
        ("ci.aggregate", "job 'required' does not run 'if: always()'", "required")
    ]


@pytest.mark.parametrize("needs", ["needs: gate", "needs: [gate, audit, lint]"])
def test_an_aggregate_that_needs_other_jobs_is_reported(needs: str) -> None:
    text = WORKFLOW.replace("needs: [gate, audit]", needs).replace(
        "  required:\n", "  lint:\n    runs-on: x\n  required:\n"
    )
    assert [rule for rule, _, _ in found(text)] == ["ci.aggregate"]
    assert "the policy requires ['audit', 'gate']" in found(text)[0][1]


def test_an_aggregate_without_the_verdict_or_the_results_is_reported() -> None:
    text = WORKFLOW.replace("./quality ci verdict", "true").replace("toJSON(needs)", "needs")
    assert found(text) == [
        ("ci.aggregate", "job 'required' does not run 'quality ci verdict'", "required"),
        ("ci.aggregate", "job 'required' does not pass 'toJSON(needs)'", "required"),
    ]


def test_missing_and_conditional_required_jobs_are_reported() -> None:
    text = WORKFLOW.replace("  audit:\n", "  scan:\n").replace(
        "  gate:\n    runs-on:", "  gate:\n    if: github.event_name == 'push'\n    runs-on:"
    )
    policy = CiPolicy(workflow=PATH, required_jobs=["gate", "scan"])
    text = text.replace("needs: [gate, audit]", "needs: [gate, scan]")
    assert found(text, policy) == [
        ("ci.aggregate", "the required job 'gate' has a condition and may be skipped", "gate")
    ]
    assert found(WORKFLOW.replace("  audit:\n", "  scan:\n")) == [
        ("ci.aggregate", "the required job 'audit' does not exist", "audit")
    ]


def test_a_browser_lane_outside_the_required_jobs_leaves_a_lane_unchecked() -> None:
    workflow = WORKFLOW.replace("./quality gate", "./quality gate --lane main").replace(
        "  audit:\n",
        "  browser:\n    runs-on: ubuntu-24.04\n    steps:\n"
        "      - run: ./quality gate --lane browser\n  audit:\n",
    )
    assert found(workflow) == [
        ("ci.lanes", "the required jobs run the gate's lane main but not 'browser'", "browser")
    ]
    policy = CiPolicy(workflow=PATH, required_jobs=["gate", "browser", "audit"])
    required = workflow.replace("needs: [gate, audit]", "needs: [gate, browser, audit]")
    assert found(required, policy) == []
