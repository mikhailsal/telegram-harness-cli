import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript.config import check_config
from quality.adapters.typescript.environment import Environments
from quality.adapters.typescript.lockfile import Lock
from quality.adapters.typescript.manifests import GROUPS, mismatches
from quality.adapters.typescript.profile import TypeScriptProfile
from quality.testkit.nodefakes import (
    PINS,
    FakeNode,
    lockfile,
    typescript_component,
    write_component,
)


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize(
    ("required", "recorded"),
    [({"x": "2.0.0"}, {"x": "1.0.0"}), ({"x": "1.0.0"}, {}), ({}, {"x": "1.0.0"})],
    ids=["changed", "added", "removed"],
)
def test_dependency_edits_are_candidate_drift(
    snapshot: Path, group: str, required: dict[str, str], recorded: dict[str, str]
) -> None:
    path = snapshot / "package.json"
    path.write_text(json.dumps({group: required}))
    [(where, message)] = mismatches(Lock(snapshot, {"": {group: recorded}}), snapshot)
    assert where == path
    assert f"{group} requires x@{required.get('x')!r}" in message
    assert f"package-lock.json records {recorded.get('x')!r}" in message
    assert "run `npm install`" in message
    assert "stage package-lock.json" in message


def test_matching_specifications_use_the_root_entry_rather_than_resolved_versions(
    snapshot: Path,
) -> None:
    manifest = {"dependencies": {"x": "^1.0.0"}, "devDependencies": {"test": "1.0.0"}}
    (snapshot / "package.json").write_text(json.dumps(manifest))
    packages = {"": manifest, "node_modules/x": {"version": "1.5.0"}}
    assert list(mismatches(Lock(snapshot, packages), snapshot)) == []


def test_new_workspaces_need_a_lock_entry(snapshot: Path) -> None:
    manifest = {"workspaces": {"packages": ["packages/*"]}}
    (snapshot / "package.json").write_text(json.dumps(manifest))
    member = snapshot / "packages" / "app"
    member.mkdir(parents=True)
    path = member / "package.json"
    path.write_text('{"dependencies": {"x": "1.0.0"}}')
    (snapshot / "packages" / "empty").mkdir()
    [(where, message)] = mismatches(Lock(snapshot, {"": manifest}), snapshot)
    assert where == path
    assert "the manifest has no package-lock.json entry" in message


def test_all_workspace_manifests_are_checked_before_an_install(snapshot: Path) -> None:
    member = write_component(snapshot / "packages" / "app")
    (member / "package-lock.json").unlink()
    root = {"workspaces": ["packages/*"], "devDependencies": PINS}
    (snapshot / "package.json").write_text(json.dumps(root))
    sibling = snapshot / "packages" / "sibling"
    sibling.mkdir()
    (sibling / "package.json").write_text('{"dependencies": {"x": "2.0.0"}}')
    extra = {
        "packages/app": {"devDependencies": PINS},
        "packages/sibling": {"dependencies": {"x": "1.0.0"}},
    }
    (snapshot / "package-lock.json").write_text(lockfile(extra=extra))
    _, report = check_config(typescript_component("packages/app"), snapshot, PINS)
    [finding] = report.found
    assert finding.rule == "typescript.lockfile"
    assert finding.location is not None
    assert finding.location.path == "packages/sibling/package.json"
    assert "requires x@'2.0.0'" in finding.message


@pytest.mark.parametrize("manifest", ["{", "[]"])
def test_an_unreadable_root_is_reported(snapshot: Path, manifest: str) -> None:
    (snapshot / "package.json").write_text(manifest)
    [(where, message)] = mismatches(Lock(snapshot, {"": {}}), snapshot)
    assert where == snapshot / "package.json"
    assert message.startswith("cannot compare the manifest with package-lock.json")


@pytest.mark.parametrize("present", [True, False])
def test_an_unreadable_or_deleted_workspace_manifest_is_reported(
    snapshot: Path, present: bool
) -> None:
    (snapshot / "package.json").write_text("{}")
    member = snapshot / "packages" / "app"
    member.mkdir(parents=True)
    if present:
        (member / "package.json").write_text("{")
    lock = Lock(snapshot, {"": {}, "packages/app": {}})
    [(where, message)] = mismatches(lock, snapshot)
    assert where == member / "package.json"
    assert message.startswith("cannot compare the workspace manifest with package-lock.json")


def test_outside_entries_and_absolute_workspace_patterns_are_not_read(snapshot: Path) -> None:
    (snapshot / "package.json").write_text('{"workspaces": ["", "/outside/*"]}')
    assert list(mismatches(Lock(snapshot, {"": {}, "../outside": {}}), snapshot)) == []


def test_a_missing_lock_root_entry_is_drift(snapshot: Path) -> None:
    (snapshot / "package.json").write_text("{}")
    [(where, message)] = mismatches(Lock(snapshot, {}), snapshot)
    assert where == snapshot / "package.json"
    assert "no package-lock.json entry" in message


def test_invalid_dependency_sections_are_reported(snapshot: Path) -> None:
    (snapshot / "package.json").write_text('{"dependencies": "x"}')
    [(_, message)] = mismatches(Lock(snapshot, {"": {}}), snapshot)
    assert "dependencies must be an object" in message


def test_drift_prevents_npm_from_starting(
    fake_node: FakeNode, snapshot: Path, tmp_path: Path
) -> None:
    directory = write_component(snapshot / "app")
    pins = {**PINS, "vitest": "0.0.0"}
    (directory / "package.json").write_text(json.dumps({"devDependencies": pins}))
    environments = Environments(lambda: fake_node.node.executable, fake_node.node.cache)
    profile = TypeScriptProfile(environments, PINS)
    [finding] = profile.prepare(typescript_component(), snapshot, tmp_path / "work")
    assert finding.rule == "typescript.lockfile"
    assert "vitest@'0.0.0'" in finding.message
    assert fake_node.calls() == []


def test_a_cache_miss_for_consistent_manifests_remains_an_environment_failure(
    fake_node: FakeNode, snapshot: Path, tmp_path: Path
) -> None:
    write_component(snapshot / "app")
    fake_node.npm("echo ENOTCACHED >&2\nexit 1\n")
    environments = Environments(lambda: fake_node.node.executable, fake_node.node.cache)
    profile = TypeScriptProfile(environments, PINS)
    with pytest.raises(ToolFailedError) as raised:
        profile.prepare(typescript_component(), snapshot, tmp_path / "work")
    assert raised.value.diagnostic.rule == "tools.failed"
    assert "ENOTCACHED" in raised.value.diagnostic.message
    assert "run `./quality bootstrap`" in raised.value.diagnostic.message
    assert len(fake_node.calls()) == 1
