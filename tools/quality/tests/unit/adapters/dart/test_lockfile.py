from pathlib import Path

import pytest
import yaml

from quality.adapters.dart.lockfile import (
    Lock,
    check_lock,
    load_lock,
    lock_directory,
    members,
    workspace_patterns,
)
from quality.adapters.native import ToolFailedError
from quality.boundedio import ReportError
from quality.testkit.dartfakes import PINS, pubspec_lock

MEMBER: dict[str, object] = {"name": "member", "resolution": "workspace"}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def manifest(path: Path, **fields: object) -> None:
    write(path / "pubspec.yaml", yaml.safe_dump(fields))


def check(directory: Path, snapshot: Path, pubspec: dict[str, object]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    check_lock(directory, snapshot, pubspec, PINS, lambda file, msg: found.append((file, msg)))
    return found


def test_workspace_entries_are_normalized() -> None:
    assert workspace_patterns({"workspace": ["./pkgs/a/", "b", 3]}) == ["pkgs/a", "b"]
    assert workspace_patterns({}) == []


def test_a_package_is_locked_in_its_own_directory(snapshot: Path) -> None:
    app = snapshot / "app"
    write(app / "pubspec.lock", pubspec_lock())
    assert lock_directory(app, snapshot, {"name": "app"}) == app
    assert lock_directory(snapshot / "other", snapshot, {"name": "other"}) is None


@pytest.mark.parametrize("entry", ["packages/app", "./packages/app/", "packages/*", "**"])
def test_a_workspace_member_is_locked_by_the_root_that_lists_it(snapshot: Path, entry: str) -> None:
    manifest(snapshot, name="root", workspace=["", entry])
    write(snapshot / "pubspec.lock", pubspec_lock())
    app = snapshot / "packages" / "app"
    manifest(app / "nested", name="decoy", workspace=["x"])
    manifest(snapshot / "packages", name="group", workspace=["other"])
    assert lock_directory(app, snapshot, MEMBER) == snapshot


def test_nested_workspaces_are_locked_at_the_outermost_root(snapshot: Path) -> None:
    manifest(snapshot, name="root", workspace=["group"])
    write(snapshot / "pubspec.lock", pubspec_lock())
    manifest(snapshot / "group", name="group", workspace=["app"], resolution="workspace")
    write(snapshot / "group" / "pubspec.lock", "stale")
    assert lock_directory(snapshot / "group" / "app", snapshot, MEMBER) == snapshot


def test_a_member_without_a_listing_root_or_lock_has_no_lock(snapshot: Path) -> None:
    app = snapshot / "packages" / "app"
    write(snapshot / "packages" / "pubspec.yaml", "{not yaml")
    assert lock_directory(app, snapshot, MEMBER) is None
    manifest(snapshot, name="root", workspace=["packages/app"])
    assert lock_directory(app, snapshot, MEMBER) is None
    # The listing root must lie inside the snapshot.
    inner = snapshot / "inner"
    manifest(snapshot, name="root", workspace=["inner/app"])
    write(snapshot / "pubspec.lock", pubspec_lock())
    assert lock_directory(inner / "app", inner, MEMBER) is None


def test_members_include_nested_workspaces_inside_the_top(tmp_path: Path) -> None:
    top = tmp_path / "repo"
    patterns = ["packages/*", "packages/a", "/absolute", "", "../outside", "missing"]
    manifest(top, name="root", workspace=patterns)
    manifest(top / "packages" / "a", name="a", workspace=["nested"])
    manifest(top / "packages" / "a" / "nested", name="nested")
    write(top / "packages" / "b" / "pubspec.yaml", "{not yaml")
    (top / "packages" / "empty").mkdir()
    manifest(tmp_path / "outside", name="outside")
    assert members(top, top) == [
        top / "packages" / "a",
        top / "packages" / "b",
        top / "packages" / "a" / "nested",
    ]


def test_a_lockfile_needs_a_packages_mapping(tmp_path: Path) -> None:
    write(tmp_path / "pubspec.lock", "sdks: {}\n")
    with pytest.raises(ReportError, match="no 'packages' mapping"):
        load_lock(tmp_path)
    write(tmp_path / "pubspec.lock", "packages:\n  1: {version: '1'}\n  bad: text\n")
    assert load_lock(tmp_path) == Lock(tmp_path, {"1": {"version": "1"}})


def test_path_packages_are_listed_with_their_paths(tmp_path: Path) -> None:
    lock = Lock(
        tmp_path,
        {
            "local": {"source": "path", "description": {"path": "../local", "relative": True}},
            "odd": {"source": "path", "description": "text"},
            "test": {"source": "hosted", "description": {"path": "ignored"}},
        },
    )
    assert lock.paths() == [("local", "../local")]


def test_a_clean_lock_has_no_findings(snapshot: Path) -> None:
    app = snapshot / "app"
    write(app / "pubspec.lock", pubspec_lock())
    assert check(app, snapshot, {"name": "app"}) == []


def test_a_missing_or_unusable_lockfile_is_reported(snapshot: Path) -> None:
    app = snapshot / "app"
    assert check(app, snapshot, {}) == [
        (
            "pubspec.lock",
            (
                "no pubspec.lock in the component or in a workspace root that lists it; "
                "run `dart pub get`"
            ),
        )
    ]
    write(app / "pubspec.lock", "[]")
    ((name, message),) = check(app, snapshot, {})
    assert name == "pubspec.lock"
    assert message.startswith("the lockfile is unusable: pubspec.lock does not hold a YAML")


def test_unpinned_foreign_and_outside_packages_are_reported(snapshot: Path) -> None:
    manifest(snapshot, name="root", workspace=["app"])
    extra: dict[str, object] = {
        "coverage": {"source": "git", "version": "1.16.0", "description": {}},
        "lints": {"source": "hosted", "version": "6.0.0", "description": {}},
        "flutter": {"source": "sdk", "version": "0.0.0", "description": "flutter"},
        "inner": {"source": "path", "description": {"path": "app", "relative": True}},
        "outer": {"source": "path", "description": {"path": "..", "relative": True}},
        "fixed": {"source": "path", "description": {"path": "/abs", "relative": False}},
    }
    pins = {"test": PINS["test"]}
    write(snapshot / "pubspec.lock", pubspec_lock(pins, extra))
    found = check(snapshot / "app", snapshot, MEMBER)
    assert {name for name, _ in found} == {"../pubspec.lock"}
    assert [message for _, message in found] == [
        "coverage is locked at '1.16.0' from 'git'; the profile pins 1.16.0",
        "lints is locked at '6.0.0' from 'hosted'; the profile pins 6.1.0",
        "flutter comes from the flutter SDK; the Dart profile supports the Dart VM only",
        "fixed is resolved from '/abs', outside the repository",
        "outer is resolved from '..', outside the repository",
    ]
    write(snapshot / "pubspec.lock", pubspec_lock({}))
    assert [message for _, message in check(snapshot / "app", snapshot, MEMBER)] == [
        f"the lockfile does not lock {name}; add `{name}: {pin}`" for name, pin in PINS.items()
    ]


def test_a_package_the_manifest_does_not_pin_is_a_tool_failure(snapshot: Path) -> None:
    write(snapshot / "pubspec.lock", pubspec_lock())
    with pytest.raises(ToolFailedError, match=r"toolchains\.toml pins no version of coverage"):
        check_lock(snapshot, snapshot, {}, {"test": "1.33.0"}, lambda _file, _msg: None)
