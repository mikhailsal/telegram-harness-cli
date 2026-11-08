from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.php.analyzer import TYPES
from quality.adapters.php.composer import Environments
from quality.adapters.php.config import CONFIG
from quality.adapters.php.fixer import PhpFixer
from quality.adapters.php.formatter import FORMAT
from quality.adapters.php.runtime import Runtime
from quality.testkit.phpfakes import PINS, REQUIRED, FakePhp, php_component, write_component

APP = php_component()
# PHP CS Fixer's `fix` rewrites `messy` to `tidy` in the files it is given; `check` lists
# the files that are still `stubborn`. PHPStan reports each file that holds `wrong`.
CS_FIXER = """\
command="$2"
listed=
for arg; do
  case "$arg" in
    /*.php)
      if [ "$command" = fix ]; then sed -i 's/messy/tidy/' "$arg"
      elif grep -q stubborn "$arg"; then listed="$listed{\\"name\\": \\"$arg\\"},"; fi ;;
  esac
done
printf '{"files": [%s]}' "${listed%,}"
[ -n "$listed" ] && exit 8
exit 0
"""
PHPSTAN = """\
files=
for arg; do
  case "$arg" in
    /*.php) grep -q wrong "$arg" && files="$files\\"$arg\\": {\\"messages\\": \
[{\\"message\\": \\"m\\", \\"line\\": 1, \\"identifier\\": \\"x.y\\"}]}," ;;
  esac
done
printf '{"files": {%s}, "errors": []}' "${files%,}"
[ -n "$files" ] && exit 1
exit 0
"""


def listing(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


@pytest.fixture
def repo(tmp_path: Path, fake_php: FakePhp) -> Path:
    root = tmp_path / "repo"
    directory = write_component(root / "app")
    (directory / "src" / "A.php").write_text("messy\n")
    (directory / "tests" / "ATest.php").write_text("messy\n")
    (directory / "vendor").mkdir()
    (directory / "vendor" / "autoload.php").write_text("<?php\n")
    fake_php.handle("php-cs-fixer", CS_FIXER)
    fake_php.handle("phpstan", PHPSTAN)
    return root


def fixer(fake_php: FakePhp) -> PhpFixer:
    php = fake_php.php
    runtime = Runtime(lambda: php.executable, lambda: php.composer, php.cache, REQUIRED)
    return PhpFixer(Environments(runtime), PINS, listing)


def test_formatting_is_copied_back(repo: Path, fake_php: FakePhp) -> None:
    files = ("app/src/A.php", "app/tests/ATest.php", "app/src/Gone.php", "app/README.md")
    assert fixer(fake_php).fix(APP, repo, files) == []
    assert (repo / "app" / "src" / "A.php").read_text() == "tidy\n"
    assert (repo / "app" / "tests" / "ATest.php").read_text() == "tidy\n"
    assert fake_php.scripts() == [
        "probe",
        "composer",
        "composer",
        "php-cs-fixer",
        "php-cs-fixer",
        "phpstan",
    ]
    validate, install, fix, check, analyse = fake_php.calls()[1:]
    copy = Path(validate[0])
    assert (copy.name, copy.parent.name) == ("app", "tree")
    assert install[0] == validate[0]
    named = [str(copy / "src" / "A.php"), str(copy / "tests" / "ATest.php")]
    assert (fix[4], fix[-2:]) == ("fix", named)
    assert (check[4], check[-2:]) == ("check", named)
    assert analyse[-2:] == named
    assert not (copy / "vendor").exists()


def test_what_remains_is_reported_and_a_concurrent_edit_is_kept(
    repo: Path, fake_php: FakePhp
) -> None:
    (repo / "app" / "src" / "A.php").write_text("messy stubborn wrong\n")
    concurrent = f"echo 'edited meanwhile' > '{repo}/app/tests/ATest.php'\n"
    fake_php.handle("php-cs-fixer", concurrent + CS_FIXER)
    found = fixer(fake_php).fix(APP, repo, ("app/src/A.php", "app/tests/ATest.php"))
    assert [(item.rule, item.location.path if item.location else "") for item in found] == [
        (FORMAT.id, "app/src/A.php"),
        (TYPES.id, "app/src/A.php"),
    ]
    assert (repo / "app" / "src" / "A.php").read_text() == "tidy stubborn wrong\n"
    assert (repo / "app" / "tests" / "ATest.php").read_text() == "edited meanwhile\n"


def test_a_rejected_configuration_is_reported_without_running_tools(
    repo: Path, fake_php: FakePhp
) -> None:
    (repo / "app" / "composer.lock").unlink()
    found = fixer(fake_php).fix(APP, repo, ("app/src/A.php",))
    assert [item.rule for item in found] == [CONFIG.id]
    assert fixer(fake_php).fix(APP, repo, ("app/README.md",)) == found
    assert fake_php.calls() == []


def test_composer_findings_stop_the_fixer(repo: Path, fake_php: FakePhp) -> None:
    fake_php.handle("composer", "echo '# General errors' >&2\necho '- bad name' >&2\nexit 2\n")
    found = fixer(fake_php).fix(APP, repo, ("app/src/A.php",))
    assert [item.message for item in found] == ["composer validate: bad name"]
    assert (repo / "app" / "src" / "A.php").read_text() == "messy\n"


def test_a_failing_formatter_is_a_tool_failure(repo: Path, fake_php: FakePhp) -> None:
    fake_php.handle("php-cs-fixer", "echo 'crashed' >&2\nexit 16\n")
    with pytest.raises(ToolFailedError, match="php-cs-fixer: exit status 16: crashed"):
        fixer(fake_php).fix(APP, repo, ("app/src/A.php",))
    assert (repo / "app" / "src" / "A.php").read_text() == "messy\n"
