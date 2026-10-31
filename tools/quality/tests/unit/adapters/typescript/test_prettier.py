from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.target import Target
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.prettier import FORMAT, prettier_argv, prettier_format
from quality.testkit.nodefakes import script, typescript_component

APP = typescript_component()
# Files named `bad` do not parse and fail the whole run; files named `messy` are listed.
PRETTIER = """\
echo "$@" >> "$HOME/calls"
for arg in "$@"; do
  case "$arg" in
    *bad*) echo "[error] ${arg#"$PWD"/}: SyntaxError: Unexpected token (1:1)" >&2; bad=1 ;;
  esac
done
[ -n "$bad" ] && exit 2
for arg in "$@"; do
  case "$arg" in
    *messy*) echo "${arg#"$PWD"/}"; echo; listed=1 ;;
  esac
done
[ -n "$listed" ] && exit 1
exit 0
"""


def run(
    node: Node, snapshot: Path, tmp_path: Path, files: tuple[str, ...], body: str = PRETTIER
) -> list[tuple[str | None, str]]:
    (snapshot / "app").mkdir(exist_ok=True)
    prettier = script(tmp_path / "prettier.sh", body)
    target = Target(APP, snapshot, files)
    found = prettier_format(target, node=node, script=prettier, home=tmp_path / "home")
    assert {d.rule for d in found} <= {FORMAT.id}
    return [(d.location.path if d.location else None, d.message) for d in found]


def test_prettier_honors_no_ignore_file(node: Node, tmp_path: Path) -> None:
    argv = prettier_argv(node, tmp_path / "cli.js", tmp_path / "ignore", "--check")
    assert argv == (
        str(node.executable),
        str(tmp_path / "cli.js"),
        "--check",
        "--ignore-path",
        str(tmp_path / "ignore"),
        "--no-error-on-unmatched-pattern",
    )


def test_formatted_files_pass(node: Node, snapshot: Path, tmp_path: Path) -> None:
    assert run(node, snapshot, tmp_path, ("app/src/a.ts",)) == []
    assert (tmp_path / "home" / "prettierignore").read_text() == ""


def test_each_listed_file_is_a_finding(node: Node, snapshot: Path, tmp_path: Path) -> None:
    files = ("app/src/messy.ts", "app/src/a.ts", "app/eslint.config.messy.js")
    message = "the file is not formatted; run `./quality fix`"
    assert run(node, snapshot, tmp_path, files) == [
        ("app/src/messy.ts", message),
        ("app/eslint.config.messy.js", message),
    ]


def test_unparsable_files_are_left_to_the_other_tools(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    files = ("app/src/bad.ts", "app/src/messy.ts", "app/src/bad2.ts")
    found = run(node, snapshot, tmp_path, files)
    assert found == [("app/src/messy.ts", "the file is not formatted; run `./quality fix`")]
    calls = (tmp_path / "home" / "calls").read_text().splitlines()
    assert len(calls) == 2
    assert "bad" not in calls[1]


def test_an_unparsable_html_file_is_a_finding(node: Node, snapshot: Path, tmp_path: Path) -> None:
    files = ("app/src/bad.css", "app/bad.html", "app/messy.html")
    assert run(node, snapshot, tmp_path, files) == [
        ("app/bad.html", "Prettier cannot parse the file: Unexpected token (1:1)"),
        ("app/messy.html", "the file is not formatted; run `./quality fix`"),
    ]


def test_a_run_of_only_unparsable_files_reports_nothing(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    assert run(node, snapshot, tmp_path, ("app/src/bad.ts",)) == []


def test_an_error_that_names_no_checked_file_fails(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    body = "echo '[error] Invalid configuration for file \"x\"' >&2\nexit 2\n"
    with pytest.raises(ToolFailedError, match="exit status 2: \\[error\\] Invalid configuration"):
        run(node, snapshot, tmp_path, ("app/src/a.ts",), body)


def test_other_exit_statuses_fail(node: Node, snapshot: Path, tmp_path: Path) -> None:
    with pytest.raises(ToolFailedError, match="prettier: exit status 3"):
        run(node, snapshot, tmp_path, ("app/src/a.ts",), "exit 3\n")
