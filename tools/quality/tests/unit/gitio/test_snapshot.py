import os
from collections.abc import Callable
from pathlib import Path

import pytest

from quality.gitio import GitioError, check_isolated, materialize, staged
from quality.testkit.gitrepo import GitRepo


@pytest.fixture
def base(tmp_path: Path) -> Path:
    directory = tmp_path / "snapshots"
    directory.mkdir()
    return directory


def files_under(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_snapshot_equals_the_candidate_tree(repo: GitRepo, base: Path) -> None:
    repo.write("src/app.py", "print('committed')\n")
    repo.write("with space/ünï\nname.txt", b"\x00\x01binary")
    repo.write("run.sh", "#!/bin/sh\n", executable=True)
    repo.git("add", "-A")
    repo.write("src/app.py", "print('unstaged')\n")
    repo.write("untracked.py", "x = 1\n")
    git = repo.repository()
    candidate = staged(git)
    with materialize(git, candidate.tree, base=base) as snapshot:
        assert files_under(snapshot.root) == {
            "run.sh": b"#!/bin/sh\n",
            "src/app.py": b"print('committed')\n",
            "with space/ünï\nname.txt": b"\x00\x01binary",
        }
        assert os.access(snapshot.root / "run.sh", os.X_OK)
        assert not os.access(snapshot.root / "src/app.py", os.X_OK)
        assert snapshot.work.is_dir()
        assert not snapshot.work.is_relative_to(snapshot.root)
        top = snapshot.root.parent
    assert not top.exists()


def test_snapshot_is_removed_when_the_body_fails(repo: GitRepo, base: Path) -> None:
    repo.write("a.txt", "a")
    repo.commit_all("first")
    git = repo.repository()
    message = "body failed"
    with pytest.raises(RuntimeError, match=message), materialize(git, "HEAD", base=base):
        raise RuntimeError(message)
    assert list(base.iterdir()) == []


def test_no_filters_run_and_line_endings_are_kept(
    repo: GitRepo, base: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "smudge-ran"
    repo.write(".gitattributes", "*.txt filter=evil eol=crlf\npointer.bin filter=lfs\n")
    repo.write("a.txt", "one\ntwo\n")
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 3\n"
    repo.write("pointer.bin", pointer)
    repo.commit_all("first")
    repo.git("config", "filter.evil.smudge", f"touch {marker}; cat")
    repo.git("config", "filter.lfs.smudge", f"touch {marker}; cat")
    git = repo.repository()
    with materialize(git, "HEAD", base=base) as snapshot:
        assert (snapshot.root / "a.txt").read_bytes() == b"one\ntwo\n"
        assert (snapshot.root / "pointer.bin").read_text() == pointer
    assert not marker.exists()


def test_internal_symlinks_are_materialized(repo: GitRepo, base: Path) -> None:
    repo.write("docs/real.md", "# Real\n")
    (repo.path / "docs" / "alias.md").symlink_to("real.md")
    (repo.path / "dangling").symlink_to("missing/file")
    repo.commit_all("links")
    with materialize(repo.repository(), "HEAD", base=base) as snapshot:
        assert (snapshot.root / "docs/alias.md").read_text() == "# Real\n"
        assert (snapshot.root / "dangling").readlink() == Path("missing/file")


@pytest.mark.parametrize("target", ["/etc/passwd", "../outside", "a/../../outside"])
def test_escaping_symlinks_are_rejected(repo: GitRepo, base: Path, target: str) -> None:
    repo.write("a/file.txt", "x")
    (repo.path / "link").symlink_to(target)
    repo.commit_all("escape")
    with pytest.raises(GitioError) as raised, materialize(repo.repository(), "HEAD", base=base):
        pass
    assert raised.value.diagnostic.rule == "git.symlink-escape"
    assert list(base.iterdir()) == []


def test_symlinks_cannot_escape_through_other_symlinks(repo: GitRepo, base: Path) -> None:
    (repo.path / "here").symlink_to(".")
    (repo.path / "sub").mkdir()
    (repo.path / "sub" / "trick").symlink_to("../here/../escape")
    repo.commit_all("chained")
    with pytest.raises(GitioError) as raised, materialize(repo.repository(), "HEAD", base=base):
        pass
    assert raised.value.diagnostic.location is not None
    assert raised.value.diagnostic.location.path == "sub/trick"


def test_gitlinks_become_empty_directories(repo: GitRepo, base: Path) -> None:
    commit = repo.commit_all("root")
    repo.git("update-index", "--add", "--cacheinfo", f"160000,{commit},vendor/lib")
    repo.git("commit", "-q", "-m", "submodule")
    with materialize(repo.repository(), "HEAD", base=base) as snapshot:
        assert [entry.path for entry in snapshot.gitlinks] == ["vendor/lib"]
        assert list((snapshot.root / "vendor/lib").iterdir()) == []


def test_project_markers_above_the_snapshot_are_rejected(repo: GitRepo, tmp_path: Path) -> None:
    repo.commit_all("first")
    polluted = tmp_path / "polluted"
    (polluted / "deeper").mkdir(parents=True)
    (polluted / "package.json").write_text("{}")
    with pytest.raises(GitioError) as raised:
        check_isolated(polluted / "deeper")
    assert raised.value.diagnostic.rule == "git.snapshot-not-isolated"
    with (
        pytest.raises(GitioError),
        materialize(repo.repository(), "HEAD", base=polluted / "deeper"),
    ):
        pass


def test_default_base_is_the_temporary_directory(repo: GitRepo) -> None:
    repo.write("a.txt", "a")
    repo.commit_all("first")
    with materialize(repo.repository(), "HEAD") as snapshot:
        assert (snapshot.root / "a.txt").read_text() == "a"


def user_state(repo: GitRepo) -> dict[str, bytes]:
    """Work tree and repository files; write-tree may add tree objects, as `git commit` does."""
    return {
        path: data
        for path, data in files_under(repo.path).items()
        if not path.startswith(".git/objects/")
    }


GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a - b\n"
TEST = "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
OLD_TEST = "def test_nothing():\n    pass\n"


def stage_good_keep_bad(repo: GitRepo) -> None:
    repo.write("src/app.py", GOOD)
    repo.write("tests/test_app.py", TEST)
    repo.git("add", "-A")
    repo.write("src/app.py", BAD)


def stage_bad_keep_good(repo: GitRepo) -> None:
    repo.write("src/app.py", BAD)
    repo.write("tests/test_app.py", TEST)
    repo.git("add", "-A")
    repo.write("src/app.py", GOOD)


def untracked_covering_test(repo: GitRepo) -> None:
    repo.write("src/app.py", GOOD)
    repo.git("add", "src/app.py")
    repo.write("tests/test_app.py", TEST)


def unstaged_covering_test(repo: GitRepo) -> None:
    repo.write("tests/test_app.py", OLD_TEST)
    repo.commit_all("old test")
    repo.write("src/app.py", GOOD)
    repo.git("add", "src/app.py")
    repo.write("tests/test_app.py", TEST)


def unstaged_test_deletion(repo: GitRepo) -> None:
    repo.write("src/app.py", GOOD)
    repo.write("tests/test_app.py", TEST)
    repo.commit_all("tested")
    (repo.path / "tests/test_app.py").unlink()


def staged_test_deletion(repo: GitRepo) -> None:
    repo.write("src/app.py", GOOD)
    repo.write("tests/test_app.py", TEST)
    repo.commit_all("tested")
    repo.git("rm", "-q", "--cached", "tests/test_app.py")


@pytest.mark.parametrize(
    ("arrange", "expected"),
    [
        (stage_good_keep_bad, {"src/app.py": GOOD, "tests/test_app.py": TEST}),
        (stage_bad_keep_good, {"src/app.py": BAD, "tests/test_app.py": TEST}),
        (untracked_covering_test, {"src/app.py": GOOD}),
        (unstaged_covering_test, {"src/app.py": GOOD, "tests/test_app.py": OLD_TEST}),
        (unstaged_test_deletion, {"src/app.py": GOOD, "tests/test_app.py": TEST}),
        (staged_test_deletion, {"src/app.py": GOOD}),
    ],
    ids=lambda value: value.__name__ if callable(value) else "",
)
def test_only_staged_content_is_tested(
    repo: GitRepo, base: Path, arrange: Callable[[GitRepo], None], expected: dict[str, str]
) -> None:
    arrange(repo)
    index = repo.path / ".git" / "index"
    before = (user_state(repo), index.read_bytes(), index.stat().st_mtime_ns)
    git = repo.repository()
    candidate = staged(git)
    with materialize(git, candidate.tree, base=base) as snapshot:
        assert files_under(snapshot.root) == {k: v.encode() for k, v in expected.items()}
    candidate.verify_unchanged()
    assert (user_state(repo), index.read_bytes(), index.stat().st_mtime_ns) == before
