from pathlib import Path

import pytest

from quality.adapters.python.config import CONFIG, NativeConfig, check_config, pytest_file
from quality.adapters.settings import CONFIG_LIMIT
from quality.diagnostics import Location
from quality.testkit.policies import ComponentSpec, build_policy

(APP,) = build_policy(ComponentSpec(root="app")).components
(ROOT,) = build_policy().components
DEFAULT = NativeConfig(ruff="pyproject.toml", pyright="pyproject.toml", pytest="pyproject.toml")
REGISTRY = 'registry = "https://pypi.org/simple"'


def problems(snapshot: Path) -> list[tuple[str, str]]:
    _, found = check_config(APP, snapshot)
    assert {d.rule for d in found.found} <= {CONFIG.id}
    return [(str(d.location.path if d.location else ""), d.message) for d in found.found]


def lock_with(component: Path, source: str) -> None:
    extra = f'\n[[package]]\nname = "dep"\nversion = "1"\nsource = {{ {source} }}\n'
    (component / "uv.lock").write_text((component / "uv.lock").read_text() + extra)


def test_a_complete_component_is_configured(component: Path, snapshot: Path) -> None:
    native, found = check_config(APP, snapshot)
    assert (native, found.found) == (DEFAULT, [])


def test_findings_are_located_in_the_repository(snapshot: Path) -> None:
    native, found = check_config(ROOT, snapshot)
    assert native is None
    (problem,) = found.found
    assert (problem.location, problem.component) == (Location("pyproject.toml"), "app")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (None, "a pyproject.toml with a [project] table is required (report not found"),
        ("[project\n", "a pyproject.toml with a [project] table is required (pyproject.toml is"),
        ("project = 1\n", "pyproject.toml needs a [project] table for uv"),
        ("#" * (CONFIG_LIMIT + 1), "a pyproject.toml with a [project] table is required (report"),
    ],
)
def test_a_usable_project_table_is_required(
    component: Path, snapshot: Path, text: str | None, message: str
) -> None:
    (component / "pyproject.toml").unlink()
    if text is not None:
        (component / "pyproject.toml").write_text(text)
    native, found = check_config(APP, snapshot)
    assert native is None
    assert [(d.location, d.message[: len(message)]) for d in found.found] == [
        (Location("app/pyproject.toml"), message)
    ]


def test_the_lockfile_is_required(component: Path, snapshot: Path) -> None:
    (component / "uv.lock").unlink()
    ((file, message),) = problems(snapshot)
    assert file == "app/uv.lock"
    assert message.startswith("the lockfile is missing or unreadable (report not found")
    assert message.endswith("; run `uv lock`")


@pytest.mark.parametrize("text", ["version = 1\n", "package = 1\n", "package = [1, 2]\n"])
def test_the_lockfile_must_lock_the_test_packages(
    component: Path, snapshot: Path, text: str
) -> None:
    (component / "uv.lock").write_text(text)
    assert problems(snapshot) == [
        ("app/uv.lock", f"the lockfile does not lock {name}; add it to the dev group")
        for name in ("coverage", "pytest", "pytest-cov")
    ]


@pytest.mark.parametrize(
    "source",
    [
        REGISTRY,
        'git = "https://github.com/a/b?rev=1#abc"',
        'url = "https://files.example/dep-1.tar.gz"',
        'directory = "../lib"',
        'path = "vendor/dep.whl"',
        "virtual = 1",
    ],
)
def test_sources_inside_the_repository_are_allowed(
    component: Path, snapshot: Path, source: str
) -> None:
    lock_with(component, source)
    assert problems(snapshot) == []


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ('editable = "../../outside"', "../../outside"),
        ('path = "/opt/dep.whl"', "/opt/dep.whl"),
        ('url = "file:///opt/dep.whl"', "file:///opt/dep.whl"),
        ('registry = "../../index"', "../../index"),
        ('directory = "~/dep"', "~/dep"),
    ],
)
def test_sources_outside_the_repository_are_rejected(
    component: Path, snapshot: Path, source: str, value: str
) -> None:
    lock_with(component, source)
    message = f"'dep' is locked from {value!r}, outside the repository"
    assert problems(snapshot) == [("app/uv.lock", message)]


def test_packages_without_a_source_table_are_skipped(component: Path, snapshot: Path) -> None:
    text = (component / "uv.lock").read_text()
    (component / "uv.lock").write_text(text + '\n[[package]]\nname = "x"\nsource = "y"\n')
    assert problems(snapshot) == []


@pytest.mark.parametrize("text", [None, "3.14\n", "3.14.8\n3.13.1\n", "cpython-3.14.8\n"])
def test_the_interpreter_is_pinned_exactly(
    component: Path, snapshot: Path, text: str | None
) -> None:
    (component / ".python-version").unlink()
    if text is not None:
        (component / ".python-version").write_text(text)
    ((file, message),) = problems(snapshot)
    assert file == "app/.python-version"
    assert "exact version" in message


def test_blank_lines_around_the_pin_are_ignored(component: Path, snapshot: Path) -> None:
    (component / ".python-version").write_text("\n3.14.8\n\n")
    assert problems(snapshot) == []


def test_tool_settings_are_checked(component: Path, snapshot: Path) -> None:
    (component / "ruff.toml").write_text('ignore = ["ALL"]\n[lint]\nselect = ["E"]\n')
    (component / "pyrightconfig.json").write_text('{"typeCheckingMode": "basic"}')
    native, found = check_config(APP, snapshot)
    assert native == NativeConfig("ruff.toml", "pyrightconfig.json", "pyproject.toml")
    assert [d.location for d in found.found] == [
        Location("app/ruff.toml"),
        Location("app/pyrightconfig.json"),
    ]


def test_pytest_files_take_precedence(component: Path) -> None:
    assert pytest_file(component) == "pyproject.toml"
    (component / ".pytest.ini").write_text("[pytest]\n")
    assert pytest_file(component) == ".pytest.ini"
    (component / "pytest.ini").write_text("[pytest]\n")
    assert pytest_file(component) == "pytest.ini"


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("pytest.ini", "[pytest]\npythonpath = src ../../outside\n"),
        (".pytest.ini", "[pytest]\npythonpath =\n    src\n    ../../outside\n"),
        ("pyproject.toml", 'pythonpath = ["src", "../../outside"]\n'),
        ("pyproject.toml", 'pythonpath = "src ../../outside"\n'),
    ],
)
def test_pytest_paths_must_stay_inside(
    component: Path, snapshot: Path, name: str, text: str
) -> None:
    if name == "pyproject.toml":
        project = '[project]\nname = "app"\n[tool.ruff.lint]\nselect = ["E"]\n'
        strict = '[tool.pyright]\ntypeCheckingMode = "strict"\n'
        text = f"{project}{strict}[tool.pytest.ini_options]\n{text}"
    (component / name).write_text(text)
    message = "pytest 'pythonpath' entry '../../outside' points outside the repository"
    assert problems(snapshot) == [(f"app/{name}", message)]


def test_native_pytest_settings_are_read(component: Path, snapshot: Path) -> None:
    project = '[project]\nname = "app"\n[tool.ruff.lint]\nselect = ["E"]\n'
    strict = '[tool.pyright]\ntypeCheckingMode = "strict"\n'
    pytest_table = '[tool.pytest]\npythonpath = ["/abs"]\n'
    (component / "pyproject.toml").write_text(project + strict + pytest_table)
    message = "pytest 'pythonpath' entry '/abs' points outside the repository"
    assert problems(snapshot) == [("app/pyproject.toml", message)]
    (component / "pyproject.toml").write_text(project + strict)
    assert problems(snapshot) == []


def test_both_pytest_tables_are_checked(component: Path, snapshot: Path) -> None:
    project = '[project]\nname = "app"\n[tool.ruff.lint]\nselect = ["E"]\n'
    strict = '[tool.pyright]\ntypeCheckingMode = "strict"\n'
    tables = '[tool.pytest]\npythonpath = ["/abs"]\n[tool.pytest.ini_options]\n'
    (component / "pyproject.toml").write_text(project + strict + tables)
    message = "pytest 'pythonpath' entry '/abs' points outside the repository"
    assert problems(snapshot) == [("app/pyproject.toml", message)]


@pytest.mark.parametrize("text", ["[pytest]\npythonpath = src\n", "[other]\nx = 1\n"])
def test_usable_ini_files_pass(component: Path, snapshot: Path, text: str) -> None:
    (component / "pytest.ini").write_text(text)
    assert check_config(APP, snapshot)[1].found == []


def test_unusable_pytest_files_are_reported(component: Path, snapshot: Path) -> None:
    (component / "pytest.ini").write_text("pythonpath = src\n")
    ((file, message),) = problems(snapshot)
    assert (file, message[:40]) == ("app/pytest.ini", "the pytest configuration is unusable: Fi")
    (component / "pytest.ini").unlink()
    (component / "pytest.ini").mkdir()
    ((file, message),) = problems(snapshot)
    assert "not a regular file" in message
