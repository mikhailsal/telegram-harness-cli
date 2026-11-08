"""The launcher as a user meets it: `./quality` in a fresh copy of the template.

The copy has no installed environment, so the first command prepares the
pinned core offline from the shared caches before the core answers.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[4]
INSTALLED = shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", "node_modules", ".dart_tool", "vendor"
)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("export") / "template"
    shutil.copytree(TEMPLATE, root, symlinks=True, ignore=INSTALLED)
    return root


def launch(root: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["./quality", *args],
        cwd=root,
        env={**os.environ, **extra},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def test_a_fresh_copy_prepares_the_core_and_answers(template: Path) -> None:
    version = launch(template, "--version")
    assert version.returncode == 0, version.stderr
    assert version.stdout.startswith("quality ")
    assert (template / "tools" / "quality" / ".venv" / "bin" / "python").exists()


def test_arguments_and_exit_statuses_pass_through_to_the_core(template: Path) -> None:
    explained = launch(template, "explain", "tests.failed")
    assert explained.returncode == 0, explained.stderr
    assert explained.stdout.startswith("tests.failed: Tests failed")
    unknown = launch(template, "explain", "no.such-rule")
    assert unknown.returncode == 2, unknown.stderr


def test_without_the_pinned_uv_other_commands_never_download(
    template: Path, tmp_path: Path
) -> None:
    refused = launch(template, "--version", XDG_CACHE_HOME=str(tmp_path / "empty"))
    assert refused.returncode == 3
    assert refused.stdout == ""
    assert refused.stderr.endswith("is not installed; run ./quality bootstrap\n")
