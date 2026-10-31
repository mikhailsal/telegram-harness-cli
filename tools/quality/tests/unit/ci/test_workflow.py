from collections.abc import Mapping
from typing import cast

import pytest
import yaml

from quality.ci.workflow import check_workflow as check
from quality.ci.workflow import (
    events,
    is_workflow,
    items,
    jobs,
    mapping,
    pinned,
    secret_uses,
    steps,
)

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
CHECKOUT = f"actions/checkout@{SHA}"
PATH = ".github/workflows/quality.yml"

CLEAN = f"""\
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
      - uses: {CHECKOUT}
        with:
          persist-credentials: false
      - run: ./quality gate --stage merge
        env:
          TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
"""


def load(text: str) -> Mapping[str, object]:
    """Parse as GitHub's loader does, keeping the key `on` that YAML 1.1 reads as true."""
    document = yaml.safe_load(text)
    assert isinstance(document, dict)
    return cast(Mapping[str, object], document)


def found(text: str) -> list[tuple[str, str, str]]:
    return [(d.rule, d.message, str(d.data["symbol"])) for d in check(PATH, load(text))]


def rules(text: str) -> list[tuple[str, str]]:
    return [(rule, symbol) for rule, _, symbol in found(text)]


def test_a_minimal_pinned_read_only_workflow_is_clean() -> None:
    assert found(CLEAN) == []


@pytest.mark.parametrize(
    "path",
    [".github/workflows/a.yml", ".github/workflows/b.yaml"],
)
def test_github_reads_yaml_files_directly_under_the_workflows_directory(path: str) -> None:
    assert is_workflow(path)


@pytest.mark.parametrize(
    "path",
    [".github/workflows/nested/a.yml", ".github/workflows/a.json", "workflows/a.yml"],
)
def test_other_paths_are_not_workflows(path: str) -> None:
    assert not is_workflow(path)


def test_the_yaml_reading_of_on_as_true_still_names_the_events() -> None:
    document = yaml.safe_load("on: [push, pull_request]\n")
    assert events(document) == {"push": None, "pull_request": None}


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        ("push", {"push": None}),
        (["push", 3], {"push": None}),
        ({"push": {"branches": ["main"]}}, {"push": {"branches": ["main"]}}),
        (None, {}),
    ],
)
def test_events_accept_every_trigger_form(trigger: object, expected: dict[str, object]) -> None:
    assert events({"on": trigger}) == expected


def test_jobs_and_steps_skip_entries_that_are_not_mappings() -> None:
    document = {"jobs": {"a": {"steps": [{"run": "x"}, "text"]}, "b": "text"}}
    assert list(jobs(document)) == ["a"]
    assert steps(jobs(document)["a"]) == [{"run": "x"}]
    assert jobs({"jobs": "text"}) == {}
    assert mapping([1]) is None
    assert items("text") == []


@pytest.mark.parametrize(
    "reference",
    [
        CHECKOUT,
        f"github/codeql-action/init@{SHA}",
        f"owner/repo/.github/workflows/reuse.yml@{SHA}",
        "./.github/actions/local",
        "docker://alpine@sha256:" + "a" * 64,
    ],
)
def test_commit_and_digest_references_are_pinned(reference: str) -> None:
    assert pinned(reference)


@pytest.mark.parametrize(
    "reference",
    [
        "actions/checkout@v7",
        "actions/checkout@main",
        f"actions/checkout@{SHA[:12]}",
        "docker://alpine:3.22",
        "docker://alpine@sha256:abc",
    ],
)
def test_tags_branches_and_short_shas_are_not_pinned(reference: str) -> None:
    assert not pinned(reference)


def test_unpinned_steps_and_reusable_workflows_are_reported() -> None:
    text = CLEAN.replace(CHECKOUT, "actions/checkout@v7") + (
        "  reuse:\n    uses: owner/repo/.github/workflows/x.yml@main\n    permissions: {}\n"
    )
    assert rules(text) == [
        ("ci.unpinned-action", "actions/checkout@v7"),
        ("ci.unpinned-action", "owner/repo/.github/workflows/x.yml@main"),
    ]


@pytest.mark.parametrize("event", ["pull_request_target", "workflow_run"])
def test_privileged_triggers_are_reported(event: str) -> None:
    text = CLEAN.replace("  merge_group:\n", f"  merge_group:\n  {event}:\n")
    assert rules(text) == [("ci.privileged-trigger", event)]


def test_a_workflow_without_top_level_permissions_is_reported() -> None:
    text = CLEAN.replace("permissions: {}\n", "")
    assert found(text) == [
        ("ci.permissions", "the workflow does not set top-level permissions", "workflow")
    ]


def test_broad_and_write_grants_are_reported_by_scope() -> None:
    text = CLEAN.replace("permissions: {}", "permissions: write-all").replace(
        "      contents: read\n", "      contents: read\n      pull-requests: write\n"
    )
    assert found(text) == [
        ("ci.permissions", "the workflow grants 'write-all'", "workflow:write-all"),
        ("ci.permissions", "job 'gate' grants 'pull-requests: write'", "gate:pull-requests"),
    ]


def test_a_checkout_that_persists_credentials_is_reported() -> None:
    text = CLEAN.replace("        with:\n          persist-credentials: false\n", "")
    assert found(text) == [
        (
            "ci.persisted-credentials",
            "job 'gate' step 1 checks out without 'persist-credentials: false'",
            "gate:1",
        )
    ]


def test_persist_credentials_may_be_the_string_false() -> None:
    assert found(CLEAN.replace("persist-credentials: false", "persist-credentials: 'false'")) == []


def test_continue_on_error_is_reported_for_jobs_and_steps() -> None:
    text = CLEAN.replace("    runs-on:", "    continue-on-error: true\n    runs-on:").replace(
        "      - run: ./quality",
        "      - continue-on-error: ${{ matrix.optional }}\n        run: ./quality",
    )
    assert rules(text) == [("ci.continue-on-error", "gate"), ("ci.continue-on-error", "gate:2")]


def test_continue_on_error_false_is_allowed() -> None:
    text = CLEAN.replace("    runs-on:", "    continue-on-error: false\n    runs-on:")
    assert found(text) == []


def test_secrets_in_workflows_that_run_candidate_code_are_reported() -> None:
    text = (
        CLEAN.replace("${{ secrets.GITHUB_TOKEN }}", "${{ secrets.DEPLOY_KEY }}")
        + "      - if: secrets.OTHER != ''\n        run: echo\n"
        + "  reuse:\n    uses: ./.github/workflows/x.yml\n    secrets: inherit\n"
        + "    permissions: {}\n"
    )
    assert found(text) == [
        (
            "ci.secret",
            "the workflow runs on 'pull_request' and reads 'secrets.DEPLOY_KEY'",
            "secrets.DEPLOY_KEY",
        ),
        (
            "ci.secret",
            "the workflow runs on 'pull_request' and reads 'secrets.OTHER != '''",
            "secrets.OTHER != ''",
        ),
        (
            "ci.secret",
            "the workflow runs on 'pull_request' and reads 'secrets: inherit'",
            "secrets: inherit",
        ),
    ]


def test_secrets_of_workflows_that_run_only_merged_code_are_allowed() -> None:
    text = CLEAN.replace("  pull_request:\n  merge_group:\n", "  push:\n").replace(
        "secrets.GITHUB_TOKEN", "secrets.DEPLOY_KEY"
    )
    assert found(text) == []


def test_secret_uses_list_each_expression_once() -> None:
    document = {
        "env": {"A": "${{ secrets.KEY }} and ${{ secrets.KEY }}", "B": "${{ github.sha }}"},
        "steps": [{"run": "${{ secrets['OTHER'] }}"}],
    }
    assert secret_uses(document) == ["secrets.KEY", "secrets['OTHER']"]
