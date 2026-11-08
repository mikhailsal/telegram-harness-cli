from pathlib import Path

import pytest

from quality.adapters.dart.analyzer import LINT
from quality.adapters.dart.config import CONFIG
from quality.adapters.dart.environment import Environments
from quality.adapters.dart.fixer import DartFixer
from quality.adapters.dart.formatter import FORMAT
from quality.adapters.native import ToolFailedError
from quality.testkit.dartfakes import PINS, FakeDart, dart_component, write_component

APP = dart_component()
# `dart fix` appends a marker to lib/a.dart; `dart format` rewrites `messy` to `tidy` in the
# files it is given and, when checking, lists the files that are still `stubborn`.
FIX = 'echo "// fixed" >> "$3/lib/a.dart"\n'
FORMAT_FILES = """\
for arg; do case "$arg" in --output=none) check=1 ;; esac; done
for arg; do
  case "$arg" in
    /*.dart)
      if [ -n "$check" ]; then grep -q stubborn "$arg" && echo "Changed $arg" && changed=1
      else sed -i 's/messy/tidy/' "$arg"; fi ;;
  esac
done
[ -n "$changed" ] && exit 1
exit 0
"""
LINTS = """\
for arg; do
  case "$arg" in
    /*.dart) grep -q lint "$arg" && printf '{"diagnostics": [{"code": "x", "type": "LINT", \
"problemMessage": "m", "location": {"file": "%s"}}]}' "$arg" && exit 1 ;;
  esac
done
echo '{"diagnostics": []}'
"""


def listing(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


@pytest.fixture
def repo(tmp_path: Path, fake_dart: FakeDart) -> Path:
    root = tmp_path / "repo"
    directory = write_component(root / "app")
    (directory / "lib" / "a.dart").write_text("messy\n")
    (directory / "test" / "a_test.dart").write_text("messy\n")
    (directory / ".dart_tool").mkdir()
    (directory / ".dart_tool" / "package_config.json").write_text("{}")
    fake_dart.handle("fix", FIX)
    fake_dart.handle("format", FORMAT_FILES)
    fake_dart.handle("analyze", LINTS)
    return root


def fixer(fake_dart: FakeDart) -> DartFixer:
    environments = Environments(lambda: fake_dart.sdk.executable, fake_dart.sdk.cache)
    return DartFixer(environments, PINS, listing)


def test_fixes_and_formatting_are_copied_back(repo: Path, fake_dart: FakeDart) -> None:
    files = ("app/lib/a.dart", "app/test/a_test.dart", "app/lib/gone.dart", "app/README.md")
    assert fixer(fake_dart).fix(APP, repo, files) == []
    assert (repo / "app" / "lib" / "a.dart").read_text() == "tidy\n// fixed\n"
    assert (repo / "app" / "test" / "a_test.dart").read_text() == "tidy\n"
    assert fake_dart.commands() == ["pub", "fix", "format", "format", "analyze"]
    pub, fix, write, check, analyze = fake_dart.calls()
    copy = Path(pub[0])
    assert copy.name == "app"
    assert copy.parent.name == "tree"
    assert fix[2:] == ["fix", "--apply", str(copy)]
    named = [str(copy / "lib" / "a.dart"), str(copy / "test" / "a_test.dart")]
    assert write[2:] == ["format", *named]
    settings = [str(copy / "pubspec.yaml"), str(copy / "analysis_options.yaml")]
    assert check[-2:] == named
    assert analyze[-4:] == [*named, *settings]
    assert not (copy / ".dart_tool" / "package_config.json").exists()


def test_what_remains_is_reported_and_a_concurrent_edit_is_kept(
    repo: Path, fake_dart: FakeDart
) -> None:
    (repo / "app" / "lib" / "a.dart").write_text("messy stubborn lint\n")
    fake_dart.handle("fix", FIX + f"echo 'edited meanwhile' > '{repo}/app/test/a_test.dart'\n")
    found = fixer(fake_dart).fix(APP, repo, ("app/lib/a.dart", "app/test/a_test.dart"))
    assert [(item.rule, item.location.path if item.location else "") for item in found] == [
        (FORMAT.id, "app/lib/a.dart"),
        (LINT.id, "app/lib/a.dart"),
    ]
    assert (repo / "app" / "lib" / "a.dart").read_text() == "tidy stubborn lint\n// fixed\n"
    assert (repo / "app" / "test" / "a_test.dart").read_text() == "edited meanwhile\n"


def test_without_dart_files_only_the_configuration_is_checked(
    repo: Path, fake_dart: FakeDart
) -> None:
    assert fixer(fake_dart).fix(APP, repo, ("app/README.md",)) == []
    assert fixer(fake_dart).fix(APP, repo, ("app/lib/gone.dart",)) == []
    (repo / "app" / "analysis_options.yaml").unlink()
    found = fixer(fake_dart).fix(APP, repo, ("app/README.md",))
    assert [item.rule for item in found] == [CONFIG.id]
    found = fixer(fake_dart).fix(APP, repo, ("app/lib/a.dart",))
    assert [item.rule for item in found] == [CONFIG.id]
    assert fake_dart.calls() == []
    assert (repo / "app" / "lib" / "a.dart").read_text() == "messy\n"


@pytest.mark.parametrize(
    ("command", "body", "message"),
    [
        ("fix", "echo 'no' >&2\nexit 1\n", "dart fix: exit status 1: no"),
        ("format", "echo 'bad' >&2\nexit 2\n", "dart format: exit status 2: bad"),
    ],
)
def test_a_failing_tool_leaves_the_working_tree_alone(
    repo: Path, fake_dart: FakeDart, command: str, body: str, message: str
) -> None:
    fake_dart.handle(command, body)
    with pytest.raises(ToolFailedError, match=message):
        fixer(fake_dart).fix(APP, repo, ("app/lib/a.dart",))
    assert (repo / "app" / "lib" / "a.dart").read_text() == "messy\n"
