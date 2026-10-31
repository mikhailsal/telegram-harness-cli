import json
import sys
from pathlib import Path

import pytest

from quality.adapters import markdown
from quality.adapters.markdown import CONFIG, lint_markdown
from quality.adapters.native import ToolFailedError
from quality.diagnostics import Diagnostic
from quality.process import Command, Completed

PYTHON = Path(sys.executable)
CLEAN = b"# Title\n\nText.\n"
CRAMPED = b"# Title\nText.\n"
HEADLESS = b"Text\n\n## Sub\n"
DISABLE_ALL = {"plugins": {"md022": {"enabled": False}, "md041": {"enabled": False}}}


def summary(found: list[Diagnostic]) -> list[tuple[str, int | None, int | None, str]]:
    return [
        (d.location.path, d.location.line, d.location.column, d.data["symbol"])
        for d in found
        if d.location is not None
    ]


@pytest.fixture
def work(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def test_clean_pages_pass(work: Path) -> None:
    assert lint_markdown([("README.md", CLEAN), ("docs/b.md", CLEAN)], work, python=PYTHON) == []
    assert json.loads((work / "pymarkdown.json").read_text()) == CONFIG
    assert sorted(path.name for path in (work / "markdown").iterdir()) == ["0.md", "1.md"]


def test_violations_map_back_to_the_original_paths(work: Path) -> None:
    pages = [("docs/ok.md", CLEAN), ("docs/a: b.md", CRAMPED), ("x.md", HEADLESS)]
    found = lint_markdown(pages, work, python=PYTHON)
    assert summary(found) == [("docs/a: b.md", 1, 1, "MD022"), ("x.md", 1, 1, "MD041")]
    assert found[0].rule == "markdown.lint"
    assert found[0].message.startswith("MD022: Headings should be surrounded by blank lines.")


def test_pages_are_linted_in_chunks(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    original = markdown.run_tool

    def counting(command: Command, *, name: str, accepted: set[int]) -> Completed:
        calls.append(command.argv[-1:])
        return original(command, name=name, accepted=accepted)

    monkeypatch.setattr(markdown, "CHUNK", 1)
    monkeypatch.setattr(markdown, "run_tool", counting)
    pages = [("a.md", HEADLESS), ("b.md", CLEAN), ("c.md", CRAMPED)]
    found = lint_markdown(pages, work, python=PYTHON)
    assert summary(found) == [("a.md", 1, 1, "MD041"), ("c.md", 1, 1, "MD022")]
    assert calls == [("0.md",), ("1.md",), ("2.md",)]


def test_configuration_outside_the_core_is_ignored(work: Path) -> None:
    for directory in (work, work.parent):
        (directory / ".pymarkdown").write_text(json.dumps(DISABLE_ALL))
        (directory / "pyproject.toml").write_text(
            "[tool.pymarkdown]\nplugins.md041.enabled = false\n"
        )
    pages = [(".pymarkdown", HEADLESS), ("docs/x.md", CRAMPED)]
    found = lint_markdown(pages, work, python=PYTHON)
    assert summary(found) == [(".pymarkdown", 1, 1, "MD041"), ("docs/x.md", 1, 1, "MD022")]


def test_a_linked_work_directory_still_maps_paths(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    found = lint_markdown([("x.md", HEADLESS)], link, python=PYTHON)
    assert summary(found) == [("x.md", 1, 1, "MD041")]


def fake_python(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-python"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return script


def test_relative_output_lines_are_accepted(work: Path, tmp_path: Path) -> None:
    script = fake_python(tmp_path, "echo; echo '1.md:2:3: MD001: Heading levels'; exit 4")
    found = lint_markdown([("a.md", CLEAN), ("b.md", CLEAN)], work, python=script)
    assert summary(found) == [("b.md", 2, 3, "MD001")]
    assert found[0].message == "MD001: Heading levels"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("echo broken >&2; exit 1", "pymarkdown: exit status 1: broken"),
        ("exit 5", "pymarkdown: exit status 5:"),
        ("exit 4", "pymarkdown: reported violations without any finding"),
        ("echo; exit 4", "pymarkdown: reported violations without any finding"),
        (
            "echo 'x.md:1:1: MD001: m'; exit 4",
            "pymarkdown: unexpected output line: x.md:1:1: MD001: m",
        ),
        (
            "echo '/elsewhere/0.md:1:1: MD001: m'; exit 4",
            "pymarkdown: unexpected output line: /elsewhere/0.md:1:1: MD001: m",
        ),
    ],
)
def test_unusable_runs_fail(work: Path, tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(ToolFailedError) as raised:
        lint_markdown([("a.md", CLEAN)], work, python=fake_python(tmp_path, body))
    assert raised.value.diagnostic.message == message
