import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript.lockfile import (
    Lock,
    check_lock,
    load_lock,
    lock_directory,
    workspace_patterns,
)
from quality.boundedio import ReportError
from quality.testkit.nodefakes import PINS, lockfile

type Found = list[tuple[str, str]]


def check(
    directory: Path, snapshot: Path, pins: dict[str, str] = PINS
) -> tuple[Lock | None, Found]:
    found: Found = []
    lock = check_lock(
        directory, snapshot, pins, lambda file, message: found.append((file, message))
    )
    return lock, found


def workspace(snapshot: Path, manifest: object, *, lock: bool = True) -> Path:
    """Write a workspace root listing `packages/*` and return the member `packages/app`."""
    (snapshot / "package.json").write_text(json.dumps(manifest))
    if lock:
        (snapshot / "package-lock.json").write_text(lockfile())
    member = snapshot / "packages" / "app"
    member.mkdir(parents=True)
    (member / "package.json").write_text("{}")
    return member


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ({"workspaces": ["./packages/*/", "tools"]}, ["packages/*", "tools"]),
        ({"workspaces": {"packages": ["apps/*"]}}, ["apps/*"]),
        ({"workspaces": {"nohoist": ["x"]}}, []),
        ({}, []),
    ],
)
def test_workspaces_are_read_in_both_forms(
    manifest: dict[str, object], expected: list[str]
) -> None:
    assert workspace_patterns(manifest) == expected


def test_a_component_lock_is_its_own(snapshot: Path) -> None:
    (snapshot / "app").mkdir()
    (snapshot / "app" / "package-lock.json").write_text(lockfile())
    assert lock_directory(snapshot / "app", snapshot) == snapshot / "app"


def test_a_workspace_member_installs_from_the_workspace_lock(snapshot: Path) -> None:
    member = workspace(snapshot, {"workspaces": ["packages/*"]})
    assert lock_directory(member, snapshot) == snapshot


def test_a_workspace_without_a_lock_has_none(snapshot: Path) -> None:
    member = workspace(snapshot, {"workspaces": ["packages/*"]}, lock=False)
    assert lock_directory(member, snapshot) is None


@pytest.mark.parametrize("manifest", [{"workspaces": ["other/*", ""]}, ["not an object"]])
def test_ancestors_that_do_not_list_the_component_are_skipped(
    snapshot: Path, manifest: object
) -> None:
    member = workspace(snapshot, manifest)
    assert lock_directory(member, snapshot) is None


def test_the_search_stops_at_the_snapshot(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"workspaces": ["**"]}')
    (tmp_path / "package-lock.json").write_text(lockfile())
    snapshot = tmp_path / "snapshot"
    (snapshot / "app").mkdir(parents=True)
    assert lock_directory(snapshot / "app", snapshot) is None


def test_node_resolution_prefers_the_nearest_entry(snapshot: Path) -> None:
    packages = {
        "": {},
        "node_modules/vitest": {"version": "1"},
        "packages/app": {"version": "0.0.0"},
        "packages/app/node_modules/vitest": {"version": "2"},
        "node_modules/app": {"link": True, "resolved": "packages/app"},
    }
    lock = Lock(snapshot, packages)
    assert lock.members() == ("packages/app",)
    member = snapshot / "packages" / "app"
    assert lock.resolve("vitest", member) == ("packages/app/node_modules/vitest", {"version": "2"})
    assert lock.resolve("vitest", snapshot) == ("node_modules/vitest", {"version": "1"})
    assert lock.resolve("eslint", member) is None


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"lockfileVersion": 1, "packages": {}}, "lockfileVersion 1 is not 2 or 3"),
        ({"lockfileVersion": 3}, "the lockfile has no 'packages' table"),
    ],
)
def test_old_or_incomplete_lockfiles_are_unusable(
    snapshot: Path, document: dict[str, object], message: str
) -> None:
    (snapshot / "package-lock.json").write_text(json.dumps(document))
    with pytest.raises(ReportError, match=message):
        load_lock(snapshot)


def test_entries_that_are_not_objects_are_ignored(snapshot: Path) -> None:
    document: dict[str, object] = {
        "lockfileVersion": 2,
        "packages": {"": {}, "node_modules/x": "broken"},
    }
    (snapshot / "package-lock.json").write_text(json.dumps(document))
    assert load_lock(snapshot) == Lock(snapshot, {"": {}})


def test_a_pinned_lockfile_passes(snapshot: Path) -> None:
    (snapshot / "package-lock.json").write_text(lockfile())
    lock, found = check(snapshot, snapshot)
    assert found == []
    assert lock is not None
    assert lock.directory == snapshot


def test_a_missing_lockfile_is_reported(snapshot: Path) -> None:
    lock, found = check(snapshot, snapshot)
    assert lock is None
    assert found == [
        (
            "package-lock.json",
            (
                "no package-lock.json in the component or in a workspace root that lists it; "
                "run `npm install`"
            ),
        )
    ]


def test_an_unusable_workspace_lock_is_named_relative_to_the_component(snapshot: Path) -> None:
    member = workspace(snapshot, {"workspaces": ["packages/*"]})
    (snapshot / "package-lock.json").write_text("{")
    lock, found = check(member, snapshot)
    assert lock is None
    [(name, message)] = found
    assert name == "../../package-lock.json"
    assert message.startswith("the lockfile is unusable: ")


def test_unpinned_and_missing_tools_are_reported(snapshot: Path) -> None:
    pins = {name: version for name, version in PINS.items() if name not in {"vitest", "@eslint/js"}}
    pins["prettier"] = "3.0.0"
    (snapshot / "package-lock.json").write_text(lockfile(pins))
    _, found = check(snapshot, snapshot)
    assert found == [
        (
            "package-lock.json",
            "node_modules/prettier is locked at '3.0.0'; the profile pins prettier 3.9.10",
        ),
        ("package-lock.json", "the lockfile does not lock vitest; add vitest@5.0.5"),
    ]


def test_a_linked_tool_has_no_pinned_version(snapshot: Path) -> None:
    link = {"link": True, "resolved": "vendor/eslint"}
    (snapshot / "package-lock.json").write_text(lockfile(extra={"node_modules/eslint": link}))
    _, found = check(snapshot, snapshot)
    assert found == [
        (
            "package-lock.json",
            "node_modules/eslint is locked at None; the profile pins eslint 10.13.0",
        )
    ]


def test_a_tool_the_manifest_does_not_pin_is_a_tool_failure(snapshot: Path) -> None:
    (snapshot / "package-lock.json").write_text(lockfile())
    pins = {name: version for name, version in PINS.items() if name != "@eslint/js"}
    with pytest.raises(ToolFailedError, match=r"toolchains\.toml pins no version of @eslint/js"):
        check(snapshot, snapshot, pins)


def test_installs_from_outside_the_repository_are_reported(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    extra = {
        "../outside": {"version": "1.0.0"},
        "node_modules/outside": {"link": True, "resolved": "../outside"},
        "node_modules/local": {"version": "1.0.0", "resolved": "file:/tmp/local.tgz"},
        "node_modules/inside": {"version": "1.0.0", "resolved": "file:vendor/inside.tgz"},
        "node_modules/remote": {"version": "1.0.0", "resolved": "git+ssh://host/x.git"},
    }
    (snapshot / "package-lock.json").write_text(lockfile(extra=extra))
    _, found = check(snapshot, snapshot)
    assert found == [
        ("package-lock.json", "../outside is installed from '../outside', outside the repository"),
        (
            "package-lock.json",
            "node_modules/outside is installed from '../outside', outside the repository",
        ),
        (
            "package-lock.json",
            "node_modules/local is installed from '/tmp/local.tgz', outside the repository",
        ),
    ]


def test_the_root_package_is_named_when_it_points_outside(snapshot: Path) -> None:
    root = {"": {"name": "app", "resolved": "../elsewhere", "link": True}}
    (snapshot / "package-lock.json").write_text(lockfile(extra=root))
    _, found = check(snapshot, snapshot)
    assert found == [
        (
            "package-lock.json",
            "the root package is installed from '../elsewhere', outside the repository",
        )
    ]
