import os
from pathlib import Path

import pytest

from quality.diagnostics import Kind
from quality.engine import provenance
from quality.engine.provenance import (
    CandidateCore,
    Provenance,
    ProvenanceError,
    candidate_core,
    in_snapshot,
    lock_findings,
    run_candidate_core,
    running_files,
)
from quality.gitio import Repository, blob_id
from quality.testkit.gitrepo import GitRepo

MAIN = """\
import sys


def main() -> int:
    print("candidate core", sys.argv[1:])
    return 7
"""


def write_core(repo: GitRepo, lock: str | None = "lock\n") -> Path:
    """Write a small core at tools/quality; return its directory."""
    repo.write("tools/quality/src/quality/__init__.py", "")
    repo.write("tools/quality/src/quality/cli/__init__.py", "")
    repo.write("tools/quality/src/quality/cli/main.py", MAIN)
    repo.write("tools/quality/src/quality/run.sh", "#!/bin/sh\n", executable=True)
    repo.write("tools/quality/toolchains.toml", "[tools]\n")
    repo.write("tools/quality/README.md", "# Core\n")
    if lock is not None:
        repo.write("tools/quality/uv.lock", lock)
    return repo.path / "tools" / "quality"


def tree_of(repo: GitRepo) -> str:
    repo.git("add", "-A")
    return repo.git("write-tree").strip()


def core_of(repo: GitRepo) -> CandidateCore:
    core = candidate_core(Repository.discover(repo.path, repo.environ()), tree_of(repo))
    assert core is not None
    return core


@pytest.mark.parametrize(
    "files",
    [
        {"README.md": "# Project\n"},
        {"tools/quality": "a file\n"},
        {"tools/quality/toolchains.toml": "[tools]\n"},
        {"tools/quality/src/other/__init__.py": ""},
    ],
)
def test_candidates_without_a_core_package_have_no_core(
    repo: GitRepo, files: dict[str, str]
) -> None:
    for name, text in files.items():
        repo.write(name, text)
    assert candidate_core(Repository.discover(repo.path, repo.environ()), tree_of(repo)) is None
    assert provenance.provenance(None, repo.path) is Provenance.NOT_APPLICABLE


def test_candidate_cores_list_the_files_that_judge(repo: GitRepo) -> None:
    write_core(repo)
    repo.write("tools/quality/src/quality/__pycache__/main.cpython-314.pyc", "cache")
    repo.write("tools/quality/tests/test_core.py", "")
    core = core_of(repo)
    assert sorted(core.files) == [
        "src/quality/__init__.py",
        "src/quality/cli/__init__.py",
        "src/quality/cli/main.py",
        "src/quality/run.sh",
        "toolchains.toml",
    ]
    assert core.files["src/quality/run.sh"].startswith("100755 ")
    assert core.lock == repo.git("rev-parse", f"{core.tree}:uv.lock").strip()
    assert core.tree == repo.git("write-tree", "--prefix=tools/quality/").strip()
    assert core.algorithm == "sha1"
    assert CandidateCore("a" * 64, {}, None).algorithm == "sha256"


def test_a_core_without_a_lock_has_no_lock(repo: GitRepo) -> None:
    write_core(repo, lock=None)
    repo.write("tools/quality/uv.lock/nested", "not a lock\n")
    assert core_of(repo).lock is None


def test_the_running_core_is_current_until_a_judging_file_changes(repo: GitRepo) -> None:
    root = write_core(repo)
    (root / "src/quality/cli/alias.py").symlink_to("main.py")
    core = core_of(repo)
    assert core.files["src/quality/cli/alias.py"].startswith("120000 ")
    (root / "src/quality/__pycache__").mkdir()
    (root / "src/quality/__pycache__/main.pyc").write_bytes(b"cache")
    (root / "README.md").write_text("# Edited\n")
    assert running_files(root) == core.files
    assert provenance.provenance(core, root) is Provenance.CURRENT
    (root / "src/quality/cli/main.py").write_text(MAIN + "# edited\n")
    assert provenance.provenance(core, root) is Provenance.STALE
    (root / "src/quality/cli/main.py").write_text(MAIN)
    (root / "toolchains.toml").unlink()
    assert provenance.provenance(core, root) is Provenance.STALE
    (root / "toolchains.toml").symlink_to("missing.toml")
    assert running_files(root)["toolchains.toml"].startswith("120000 ")


# Per helper: the judged files, the source file first and the lockfile last;
# the tracked files that do not judge; and the directory its installation writes.
@pytest.mark.parametrize(
    ("helper", "judged", "unjudged", "installed"),
    [
        ("typescript", ("src/main.ts", "package.json"), ("tests/main.test.ts",), "node_modules"),
        (
            "dart",
            ("lib/cli.dart", "bin/main.dart", "pubspec.yaml", "pubspec.lock"),
            ("test/cli_test.dart", "analysis_options.yaml"),
            ".dart_tool",
        ),
        (
            "php",
            ("src/Cli.php", "bin/main.php", "composer.json", "composer.lock"),
            ("tests/CliTest.php",),
            "vendor",
        ),
    ],
)
def test_the_helpers_sources_manifests_and_locks_judge(
    repo: GitRepo, helper: str, judged: tuple[str, ...], unjudged: tuple[str, ...], installed: str
) -> None:
    root = write_core(repo)
    source, lock = judged[0], judged[-1]
    # A tracked file inside an installation directory of a source tree does not judge.
    nested = f"{source.partition('/')[0]}/{installed}/index"
    for name in (*judged, *unjudged, nested):
        repo.write(f"tools/quality/helpers/{helper}/{name}", f"# {name}\n")
    core = core_of(repo)
    assert {path for path in core.files if path.startswith("helpers/")} == {
        f"helpers/{helper}/{name}" for name in judged
    }
    files = root / "helpers" / helper
    (files / installed / "package").mkdir(parents=True)
    (files / installed / "package" / "index").write_text("installed\n")
    for name in (*unjudged, nested):
        (files / name).write_text("# edited\n")
    assert provenance.provenance(core, root) is Provenance.CURRENT
    (files / source).write_text("# edited\n")
    assert provenance.provenance(core, root) is Provenance.STALE
    (files / source).write_text(f"# {source}\n")
    assert provenance.provenance(core, root) is Provenance.CURRENT
    (files / lock).write_text("# changed\n")
    assert provenance.provenance(core, root) is Provenance.STALE


def test_running_files_mark_special_files(tmp_path: Path) -> None:
    package = tmp_path / "src" / "quality"
    package.mkdir(parents=True)
    os.mkfifo(package / "pipe")
    (package / "main.py").write_bytes(b"x = 1\n")
    found = running_files(tmp_path, "sha256")
    assert found == {
        "src/quality/pipe": "special",
        "src/quality/main.py": f"100644 {blob_id(b'x = 1\n', algorithm='sha256')}",
    }


def test_lockfiles_must_match_the_synced_environment(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"lock\n")
    core = CandidateCore("a" * 40, {}, blob_id(b"lock\n"))
    assert lock_findings(core, lock) == []
    assert lock_findings(CandidateCore("a" * 40, {}, None), tmp_path / "missing") == []
    lock.write_bytes(b"other\n")
    for found in (lock_findings(core, lock), lock_findings(core, tmp_path / "missing")):
        [diagnostic] = found
        assert diagnostic.rule == "core.environment"
        assert diagnostic.kind is Kind.ENVIRONMENT
        assert diagnostic.location is not None
        assert diagnostic.location.path == "tools/quality/uv.lock"
        assert "which the core environment was synced from" in diagnostic.message


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/var/quality-snapshot-x1/candidate", True),
        ("/var/quality-snapshot-x1/work", False),
        ("/var/project/candidate", False),
        ("candidate", False),
    ],
)
def test_snapshot_cores_are_recognized(path: str, expected: bool) -> None:
    assert in_snapshot(Path(path)) is expected


def test_the_candidate_core_runs_the_command(
    repo: GitRepo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    write_core(repo)
    core = core_of(repo)
    monkeypatch.chdir(repo.path)
    git = Repository.discover(repo.path, repo.environ())
    assert run_candidate_core(git, core, ["check", "--json"], repo.environ()) == 7
    assert capfd.readouterr().out == "candidate core ['check', '--json']\n"


def nothing_runs(root: Path, algorithm: str) -> dict[str, str]:
    return {}


def test_a_materialized_core_that_differs_is_an_internal_error(
    repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_core(repo)
    core = core_of(repo)
    monkeypatch.setattr(provenance, "running_files", nothing_runs)
    git = Repository.discover(repo.path, repo.environ())
    with pytest.raises(ProvenanceError, match=f"differs from tree {core.tree}"):
        run_candidate_core(git, core, ["check"], repo.environ())
