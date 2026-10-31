import pytest

from quality.gitio import Hunk, added_lines, map_line, tree_changes
from quality.gitio.diff import carry_lines, parse_hunks
from quality.testkit.gitrepo import GitRepo


def lines(count: int, *, prefix: str = "line") -> str:
    return "".join(f"{prefix} {n}\n" for n in range(1, count + 1))


def trees(repo: GitRepo, old: str, new: str) -> tuple[str, str]:
    git = repo.repository()
    return git.tree_of(old), git.tree_of(new)


def test_tree_changes_report_status_paths_and_modes(repo: GitRepo) -> None:
    repo.write("keep.py", lines(20))
    repo.write("edit.py", "a\n")
    repo.write("gone.py", "g\n")
    repo.write("tool", "#!/bin/sh\n")
    first = repo.commit_all("first")
    repo.git("mv", "keep.py", "moved.py")
    repo.write("edit.py", "b\n")
    repo.git("rm", "-q", "gone.py")
    repo.write("new.py", "n\n")
    (repo.path / "tool").chmod(0o755)
    second = repo.commit_all("second")
    changes = {c.path: c for c in tree_changes(repo.repository(), *trees(repo, first, second))}
    assert {path: c.status for path, c in changes.items()} == {
        "edit.py": "M",
        "gone.py": "D",
        "moved.py": "R",
        "new.py": "A",
        "tool": "M",
    }
    assert changes["moved.py"].old_path == "keep.py"
    assert changes["edit.py"].old_path is None
    assert (changes["tool"].old_mode, changes["tool"].new_mode) == ("100644", "100755")
    assert changes["new.py"].old_mode == "000000"
    assert not changes["new.py"].old_is_file
    assert changes["new.py"].new_is_file


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (b"@@ -3 +3 @@", Hunk(3, 1, 3, 1)),
        (b"@@ -3,0 +4,2 @@ def f():", Hunk(3, 0, 4, 2)),
        (b"@@ -5,2 +4,0 @@", Hunk(5, 2, 4, 0)),
        (b"@@ -0,0 +1,3 @@", Hunk(0, 0, 1, 3)),
    ],
)
def test_parse_hunks(header: bytes, expected: Hunk) -> None:
    patch = b"diff --git a/x b/x\n--- a/x\n+++ b/x\n" + header + b"\n+text\n"
    assert parse_hunks(patch) == (expected,)


def test_map_line_follows_insertions_deletions_and_replacements() -> None:
    hunks = [Hunk(2, 0, 3, 2), Hunk(5, 1, 7, 3), Hunk(9, 2, 12, 0)]
    # Lines 1 and 2 precede the insertion after line 2.
    assert [map_line(hunks, n) for n in (1, 2)] == [1, 2]
    assert [map_line(hunks, n) for n in (3, 4)] == [5, 6]
    assert map_line(hunks, 5) is None
    assert [map_line(hunks, n) for n in (6, 7, 8)] == [10, 11, 12]
    assert [map_line(hunks, n) for n in (9, 10)] == [None, None]
    assert map_line(hunks, 11) == 13
    assert map_line([Hunk(0, 0, 1, 2)], 1) == 3


def test_added_lines_cover_new_and_modified_text_files_only(repo: GitRepo) -> None:
    repo.write("edit.py", lines(5))
    repo.write("gone.py", "g\n")
    repo.write("image.png", b"\x89PNG\x00\x00")
    repo.write("rename_me.py", lines(30))
    first = repo.commit_all("first")
    repo.write("edit.py", lines(5).replace("line 2\n", "changed\n") + "appended")
    repo.git("rm", "-q", "gone.py")
    repo.write("image.png", b"\x89PNG\x00\x01")
    repo.write("new.py", lines(3))
    repo.write("new.bin", b"\x00\x01\n")
    repo.write("empty.py", "")
    repo.write("no_newline.py", "a\nb")
    repo.git("mv", "rename_me.py", "renamed.py")
    repo.write("renamed.py", lines(30).replace("line 30\n", "last\n"))
    (repo.path / "link").symlink_to("new.py")
    second = repo.commit_all("second")
    assert added_lines(repo.repository(), *trees(repo, first, second)) == {
        "edit.py": frozenset({2, 6}),
        "new.py": frozenset({1, 2, 3}),
        "no_newline.py": frozenset({1, 2}),
        "renamed.py": frozenset({30}),
    }


def test_type_change_from_symlink_counts_every_line(repo: GitRepo) -> None:
    repo.write("target.py", "t\n")
    (repo.path / "alias.py").symlink_to("target.py")
    first = repo.commit_all("first")
    (repo.path / "alias.py").unlink()
    repo.write("alias.py", "real\nfile\n")
    second = repo.commit_all("second")
    assert added_lines(repo.repository(), *trees(repo, first, second)) == {
        "alias.py": frozenset({1, 2})
    }


def test_carry_lines_through_edits_renames_and_deletions(repo: GitRepo) -> None:
    repo.write("same.py", lines(3))
    repo.write("edited.py", lines(10))
    repo.write("moved.py", lines(30))
    repo.write("deleted.py", lines(2))
    repo.write("binary.dat", lines(2))
    repo.write("linked.py", lines(2))
    first = repo.commit_all("first")
    repo.write("edited.py", "inserted\n" + lines(10).replace("line 5\n", "five\n"))
    repo.git("mv", "moved.py", "target.py")
    repo.git("rm", "-q", "deleted.py")
    repo.write("binary.dat", b"\x00binary\n")
    (repo.path / "linked.py").unlink()
    (repo.path / "linked.py").symlink_to("same.py")
    second = repo.commit_all("second")
    tracked = {
        "same.py": frozenset({1, 3}),
        "edited.py": frozenset({4, 5, 6}),
        "moved.py": frozenset({30}),
        "deleted.py": frozenset({1}),
        "binary.dat": frozenset({1}),
        "linked.py": frozenset({1}),
    }
    assert carry_lines(repo.repository(), tracked, *trees(repo, first, second)) == {
        "same.py": frozenset({1, 3}),
        "edited.py": frozenset({5, 7}),
        "target.py": frozenset({30}),
    }
