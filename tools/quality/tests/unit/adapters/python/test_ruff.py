import itertools
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.python.ruff import ruff_format, ruff_lint, ruff_settings
from quality.adapters.target import Target
from quality.diagnostics import Location
from quality.testkit.policies import ComponentSpec, build_policy

type Fake = Callable[[str, int], Path]
type Found = list[tuple[str, str]]

(APP,) = build_policy(ComponentSpec(root="app")).components
SELECT = '[lint]\nselect = ["E"]\n'
FILES = ("app/src/pkg/a.py", "app/src/pkg/b.py", "app/src/pkg/c.py")


def check(directory: Path, snapshot: Path) -> tuple[str, Found]:
    found: Found = []
    name = ruff_settings(directory, snapshot, lambda file, message: found.append((file, message)))
    return name, found


def sources(directory: Path) -> None:
    (directory / "src/pkg/a.py").write_text("import os\nx=1\n")
    (directory / "src/pkg/b.py").write_text("def f(:\n")
    (directory / "src/pkg/c.py").write_text("x = 1\n")


def test_the_project_settings_are_read(component: Path, snapshot: Path) -> None:
    assert check(component, snapshot) == ("pyproject.toml", [])


def test_ruff_files_take_precedence(component: Path, snapshot: Path) -> None:
    (component / "ruff.toml").write_text('select = ["E"]\n')
    assert check(component, snapshot) == ("ruff.toml", [])
    (component / ".ruff.toml").write_text("line-length = 100\n")
    message = "select Ruff lint rules explicitly with lint.select"
    assert check(component, snapshot) == (".ruff.toml", [(".ruff.toml", message)])


def test_a_project_without_ruff_settings_selects_nothing(snapshot: Path) -> None:
    (snapshot / "pyproject.toml").write_text('[project]\nname = "app"\n')
    message = "select Ruff lint rules explicitly with lint.select"
    assert check(snapshot, snapshot) == ("pyproject.toml", [("pyproject.toml", message)])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (f'ignore = ["ALL"]\n{SELECT}', "Ruff must not ignore ALL rules"),
        (f'{SELECT}extend-ignore = ["E501", "ALL"]\n', "Ruff must not ignore ALL rules"),
        (
            f'{SELECT}[per-file-ignores]\n"tests/*" = ["ALL"]\n',
            "Ruff per-file ignores must not exempt 'tests/*' from ALL rules",
        ),
        (
            f'{SELECT}[lint.extend-per-file-ignores]\n"x.py" = ["E501", "ALL"]\n',
            "Ruff per-file ignores must not exempt 'x.py' from ALL rules",
        ),
    ],
)
def test_blanket_ignores_are_rejected(
    component: Path, snapshot: Path, text: str, message: str
) -> None:
    (component / "ruff.toml").write_text(text)
    assert check(component, snapshot) == ("ruff.toml", [("ruff.toml", message)])


def test_specific_ignores_are_allowed(component: Path, snapshot: Path) -> None:
    text = f'{SELECT}ignore = ["E501"]\n[lint.per-file-ignores]\n"x.py" = ["E402"]\n'
    (component / "ruff.toml").write_text(text)
    assert check(component, snapshot) == ("ruff.toml", [])


def test_extended_files_are_checked(component: Path, snapshot: Path) -> None:
    (component / "ruff.toml").write_text('extend = "../shared/base.toml"\n')
    (snapshot / "shared").mkdir()
    (snapshot / "shared/base.toml").write_text(f'{SELECT}ignore = ["ALL"]\n')
    found = [("../shared/base.toml", "Ruff must not ignore ALL rules")]
    assert check(component, snapshot) == ("ruff.toml", found)


def test_an_extended_project_file_is_read_from_its_tool_table(
    component: Path, snapshot: Path
) -> None:
    (component / "ruff.toml").write_text('extend = "../pyproject.toml"\n')
    (snapshot / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["F"]\n')
    assert check(component, snapshot) == ("ruff.toml", [])
    (snapshot / "pyproject.toml").write_text('[project]\nname = "root"\n')
    message = "select Ruff lint rules explicitly with lint.select"
    assert check(component, snapshot) == ("ruff.toml", [("ruff.toml", message)])


@pytest.mark.parametrize("parent", ["../../outside.toml", "/etc/ruff.toml", "~/r.toml", "$X.toml"])
def test_extending_outside_the_repository_fails(
    component: Path, snapshot: Path, parent: str
) -> None:
    (component / "ruff.toml").write_text(f'extend = "{parent}"\n{SELECT}')
    message = f"Ruff 'extend = {parent!r}' points outside the repository"
    assert check(component, snapshot) == ("ruff.toml", [("ruff.toml", message)])


def test_unreadable_extended_files_fail(component: Path, snapshot: Path) -> None:
    (component / "ruff.toml").write_text('extend = "base.toml"\n')
    _, found = check(component, snapshot)
    assert [file for file, _ in found] == ["base.toml"]
    assert "not a regular file" in found[0][1]
    (component / "base.toml").write_text("[lint\n")
    _, found = check(component, snapshot)
    assert found[0][1].startswith("base.toml is not valid TOML: ")


def test_a_non_string_extend_is_not_followed(component: Path, snapshot: Path) -> None:
    (component / "ruff.toml").write_text(f"extend = 1\n{SELECT}")
    assert check(component, snapshot) == ("ruff.toml", [])


def chain(directory: Path, length: int) -> None:
    names = ["ruff.toml", *(f"c{index}.toml" for index in range(1, length))]
    for name, parent in itertools.pairwise(names):
        (directory / name).write_text(f'extend = "{parent}"\n')
    (directory / names[-1]).write_text(SELECT)


def test_extend_chains_are_limited(component: Path, snapshot: Path) -> None:
    chain(component, 6)
    assert check(component, snapshot) == ("ruff.toml", [])
    chain(component, 7)
    message = "Ruff 'extend' chain is circular or longer than 5 files"
    assert check(component, snapshot) == ("ruff.toml", [("ruff.toml", message)])
    (component / "ruff.toml").write_text('extend = "ruff.toml"\n')
    assert check(component, snapshot) == ("ruff.toml", [("ruff.toml", message)])


def test_format_findings_skip_syntax_errors(component: Path, snapshot: Path, tools: Path) -> None:
    sources(component)
    found = ruff_format(Target(APP, snapshot, FILES), ruff=tools / "ruff", config="pyproject.toml")
    assert [(d.rule, d.location, d.component) for d in found] == [
        ("python.format", Location("app/src/pkg/a.py", 2, 1), "app")
    ]
    assert found[0].message == "the file is not formatted; run `./quality fix`"
    clean = Target(APP, snapshot, FILES[2:])
    assert ruff_format(clean, ruff=tools / "ruff", config="pyproject.toml") == []


def test_lint_findings_include_syntax_errors(component: Path, snapshot: Path, tools: Path) -> None:
    sources(component)
    found = ruff_lint(Target(APP, snapshot, FILES), ruff=tools / "ruff", config="pyproject.toml")
    first = found[0]
    assert (first.rule, first.location) == ("python.lint", Location("app/src/pkg/a.py", 1, 8))
    assert first.message == "F401: `os` imported but unused"
    syntax = {d.location.path for d in found[1:] if d.location is not None}
    assert syntax == {"app/src/pkg/b.py"}
    assert all(d.message.startswith("invalid-syntax: ") for d in found[1:])
    clean = Target(APP, snapshot, FILES[2:])
    assert ruff_lint(clean, ruff=tools / "ruff", config="pyproject.toml") == []


def test_exclusions_do_not_apply_to_named_files(
    component: Path, snapshot: Path, tools: Path
) -> None:
    sources(component)
    excluded = '["src/pkg/a.py"]'
    text = f'exclude = {excluded}\n[lint]\nselect = ["F"]\nexclude = {excluded}\n'
    (component / "ruff.toml").write_text(f"{text}[format]\nexclude = {excluded}\n")
    target = Target(APP, snapshot, FILES[:1])
    assert len(ruff_format(target, ruff=tools / "ruff", config="ruff.toml")) == 1
    assert len(ruff_lint(target, ruff=tools / "ruff", config="ruff.toml")) == 1


def finding(location: object) -> str:
    entry = {"filename": "src/pkg/a.py", "code": "X1", "message": "m", "location": location}
    return json.dumps([entry])


def test_findings_without_a_position_have_no_line(
    component: Path, snapshot: Path, fake: Fake
) -> None:
    target = Target(APP, snapshot, FILES[:1])
    found = ruff_lint(target, ruff=fake(finding("bad"), 1), config="pyproject.toml")
    assert [(d.message, d.location) for d in found] == [("X1: m", Location("app/src/pkg/a.py"))]
    found = ruff_lint(target, ruff=fake(finding({"row": 4}), 1), config="pyproject.toml")
    assert found[0].location == Location("app/src/pkg/a.py", 4, None)


@pytest.mark.parametrize(
    ("stdout", "code", "message"),
    [
        ("[]", 1, "ruff: exit status 1 with 0 findings: fake stderr"),
        (finding({"row": 1, "column": 1}), 0, "ruff: exit status 0 with 1 findings: fake stderr"),
        ("[]", 3, "ruff: exit status 3: fake stderr"),
        ("error: no config", 0, "ruff: malformed JSON in ruff output: "),
        ('{"a": 1}', 1, "ruff: output is not a list of findings"),
    ],
)
def test_inconsistent_ruff_output_fails(
    component: Path, snapshot: Path, fake: Fake, stdout: str, code: int, message: str
) -> None:
    target = Target(APP, snapshot, FILES[:1])
    with pytest.raises(ToolFailedError) as raised:
        ruff_lint(target, ruff=fake(stdout, code), config="pyproject.toml")
    assert raised.value.diagnostic.message.startswith(message)
