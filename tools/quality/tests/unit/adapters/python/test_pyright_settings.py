import itertools
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from quality.adapters.python.pyright import pyright_settings

type Found = list[tuple[str, str]]

STRICT = {"typeCheckingMode": "strict"}
FILE = "pyrightconfig.json"
NOT_STRICT = 'run Pyright with typeCheckingMode = "strict"'


def check(directory: Path, snapshot: Path) -> tuple[str, Found]:
    found: Found = []
    name = pyright_settings(
        directory, snapshot, lambda file, message: found.append((file, message))
    )
    return name, found


def config(path: Path, settings: Mapping[str, object]) -> None:
    path.write_text(json.dumps(settings))


def test_the_project_settings_are_read(component: Path, snapshot: Path) -> None:
    assert check(component, snapshot) == ("pyproject.toml", [])
    config(component / FILE, STRICT)
    assert check(component, snapshot) == (FILE, [])


@pytest.mark.parametrize("mode", ["standard", True, None])
def test_strict_mode_is_required(component: Path, snapshot: Path, mode: object) -> None:
    config(component / FILE, {"typeCheckingMode": mode})
    assert check(component, snapshot) == (FILE, [(FILE, NOT_STRICT)])


def test_a_project_without_pyright_settings_is_not_strict(snapshot: Path) -> None:
    (snapshot / "pyproject.toml").write_text('[project]\nname = "app"\n')
    assert check(snapshot, snapshot) == ("pyproject.toml", [("pyproject.toml", NOT_STRICT)])


def test_the_nearest_mode_wins(component: Path, snapshot: Path) -> None:
    config(component / "base.json", STRICT)
    config(component / FILE, {"extends": "base.json"})
    assert check(component, snapshot) == (FILE, [])
    config(component / FILE, {"extends": "base.json", "typeCheckingMode": "basic"})
    assert check(component, snapshot) == (FILE, [(FILE, NOT_STRICT)])


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        ("ignore", "it hides diagnostics in matching files"),
        ("defineConstant", "it can make code unreachable and therefore unchecked"),
    ],
)
def test_forbidden_settings(component: Path, snapshot: Path, key: str, reason: str) -> None:
    config(component / FILE, {**STRICT, key: {}})
    message = f"Pyright '{key}' is not allowed: {reason}"
    assert check(component, snapshot) == (FILE, [(FILE, message)])


@pytest.mark.parametrize(
    ("settings", "setting"),
    [
        ({"reportUnusedImport": "warning"}, "reportUnusedImport = 'warning'"),
        ({"reportMissingImports": "none"}, "reportMissingImports = 'none'"),
        ({"reportPrivateUsage": "information"}, "reportPrivateUsage = 'information'"),
        ({"reportUnknownMemberType": False}, "reportUnknownMemberType = False"),
        ({"strictListInference": False}, "strictListInference = false"),
        (
            {"executionEnvironments": [{"root": "src", "reportMissingImports": "none"}, "x"]},
            "reportMissingImports = 'none'",
        ),
    ],
)
def test_weakened_rules_are_rejected(
    component: Path, snapshot: Path, settings: dict[str, object], setting: str
) -> None:
    config(component / FILE, {**STRICT, **settings})
    assert check(component, snapshot) == (
        FILE,
        [(FILE, f"Pyright '{setting}' weakens strict mode")],
    )


def test_stricter_and_unrelated_settings_are_allowed(component: Path, snapshot: Path) -> None:
    settings = {
        "reportUnusedImport": "error",
        "reportMissingTypeStubs": True,
        "strictListInference": True,
        "pythonVersion": "3.14",
        "executionEnvironments": "src",
        "extraPaths": ["src", 1],
        "venvPath": 1,
        "venv": ".venv",
    }
    config(component / FILE, {**STRICT, **settings})
    assert check(component, snapshot) == (FILE, [])


@pytest.mark.parametrize(
    ("settings", "value"),
    [
        ({"stubPath": "/stubs"}, "/stubs"),
        ({"typeshedPath": "~/typeshed"}, "~/typeshed"),
        ({"venvPath": "$VENVS"}, "$VENVS"),
        ({"root": "../.."}, "../.."),
        ({"extraPaths": ["src", "../../lib"]}, "../../lib"),
        ({"executionEnvironments": [{"root": "../../x"}]}, "../../x"),
    ],
)
def test_paths_outside_the_repository_are_rejected(
    component: Path, snapshot: Path, settings: dict[str, object], value: str
) -> None:
    config(component / FILE, {**STRICT, **settings})
    message = f"Pyright path {value!r} points outside the repository"
    assert check(component, snapshot) == (FILE, [(FILE, message)])


def test_a_tracked_environment_is_rejected(component: Path, snapshot: Path) -> None:
    (component / ".venv").mkdir()
    (component / "envs" / "v").mkdir(parents=True)
    message = "Pyright venv {!r} is tracked; it would replace the locked environment"
    for settings, venv in (({"venv": ".venv"}, ".venv"), ({"venvPath": "envs", "venv": "v"}, "v")):
        config(component / FILE, {**STRICT, **settings})
        assert check(component, snapshot) == (FILE, [(FILE, message.format(venv))])


def test_extended_files_are_checked(component: Path, snapshot: Path) -> None:
    (snapshot / "shared").mkdir()
    config(snapshot / "shared/base.json", {**STRICT, "ignore": ["src"]})
    config(component / FILE, {"extends": "../shared/base.json"})
    message = "Pyright 'ignore' is not allowed: it hides diagnostics in matching files"
    assert check(component, snapshot) == (FILE, [("../shared/base.json", message)])


def test_an_extended_project_file_is_read_from_its_tool_table(
    component: Path, snapshot: Path
) -> None:
    config(component / FILE, {"extends": "../pyproject.toml"})
    (snapshot / "pyproject.toml").write_text('[tool.pyright]\ntypeCheckingMode = "strict"\n')
    assert check(component, snapshot) == (FILE, [])
    (snapshot / "pyproject.toml").write_text('[project]\nname = "root"\n')
    assert check(component, snapshot) == (FILE, [(FILE, NOT_STRICT)])


def test_extending_outside_the_repository_fails(component: Path, snapshot: Path) -> None:
    config(component / FILE, {**STRICT, "extends": "../../base.json"})
    message = "Pyright 'extends = '../../base.json'' points outside the repository"
    assert check(component, snapshot) == (FILE, [(FILE, message)])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (None, "not a regular file"),
        ("[1]", "base.json does not hold a JSON object"),
        ("{,}", "base.json is not plain JSON: "),
    ],
)
def test_unreadable_extended_files_fail(
    component: Path, snapshot: Path, text: str | None, message: str
) -> None:
    config(component / FILE, {"extends": "base.json"})
    if text is not None:
        (component / "base.json").write_text(text)
    name, found = check(component, snapshot)
    assert (name, [file for file, _ in found]) == (FILE, ["base.json"])
    assert message in found[0][1]


def test_extends_chains_are_limited(component: Path, snapshot: Path) -> None:
    names = [FILE, *(f"c{index}.json" for index in range(1, 7))]
    for name, parent in itertools.pairwise(names):
        config(component / name, {"extends": parent})
    config(component / names[-1], STRICT)
    message = "Pyright 'extends' chain is circular or longer than 5 files"
    assert check(component, snapshot) == (FILE, [(FILE, message)])
    config(component / names[-2], STRICT)
    assert check(component, snapshot) == (FILE, [])
    config(component / FILE, {"extends": FILE})
    assert check(component, snapshot) == (FILE, [(FILE, message)])
