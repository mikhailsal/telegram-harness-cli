import pytest

from quality.gitio import GitioError, HistoryBlob, Repository, RevisionRange, new_blobs
from quality.gitio.history import HISTORY_OUTPUT_LIMIT
from quality.process import Completed
from quality.testkit.gitrepo import GitRepo


def blob_id(repo: GitRepo, rev: str, path: str) -> str:
    return repo.git("rev-parse", f"{rev}:{path}").strip()


def test_range_arguments() -> None:
    assert RevisionRange(None, "tip").arguments() == ("tip",)
    assert RevisionRange("base", "tip").arguments() == ("tip", "^base")


def test_blobs_deleted_before_the_tip_are_found(repo: GitRepo) -> None:
    repo.write("keep.txt", "kept\n")
    base = repo.commit_all("base")
    repo.write("leak.txt", "removed later\n")
    added = repo.commit_all("add")
    leaked = blob_id(repo, added, "leak.txt")
    (repo.path / "leak.txt").unlink()
    repo.write("dir/new.txt", "new\n")
    tip = repo.commit_all("delete")
    blobs = new_blobs(repo.repository(), RevisionRange(base, tip))
    assert blobs == (
        HistoryBlob(blob_id(repo, tip, "dir/new.txt"), 4, "dir/new.txt"),
        HistoryBlob(leaked, 14, "leak.txt"),
    )


def test_no_base_means_the_whole_history(repo: GitRepo) -> None:
    repo.write("a.txt", "a")
    first = repo.commit_all("first")
    repo.write("a.txt", "b")
    tip = repo.commit_all("second")
    blobs = new_blobs(repo.repository(), RevisionRange(None, tip))
    assert {(blob.path, blob.size) for blob in blobs} == {("a.txt", 1)}
    assert {blob.oid for blob in blobs} == {
        blob_id(repo, first, "a.txt"),
        blob_id(repo, tip, "a.txt"),
    }


def test_an_empty_range_has_no_blobs(repo: GitRepo) -> None:
    repo.write("a.txt", "a")
    tip = repo.commit_all("first")
    assert new_blobs(repo.repository(), RevisionRange(tip, tip)) == ()


def test_unusual_paths_and_annotated_tags(repo: GitRepo) -> None:
    base = repo.commit_all("empty")
    repo.write("odd\nname with space.txt", "x")
    repo.commit_all("odd")
    repo.git("tag", "-a", "v1", "-m", "release")
    blobs = new_blobs(repo.repository(), RevisionRange(base, "v1"))
    assert [blob.path for blob in blobs] == ["odd\nname with space.txt"]


def test_blobs_introduced_by_a_merge_resolution(repo: GitRepo) -> None:
    repo.write("a.txt", "base\n")
    base = repo.commit_all("base")
    repo.git("checkout", "-q", "-b", "side")
    repo.write("a.txt", "side\n")
    repo.commit_all("side")
    repo.git("checkout", "-q", "main")
    repo.write("a.txt", "main\n")
    repo.commit_all("main")
    repo.run_git("merge", "-q", "side")
    repo.write("a.txt", "resolved\n")
    tip = repo.commit_all("merge")
    blobs = new_blobs(repo.repository(), RevisionRange(base, tip))
    assert blob_id(repo, tip, "a.txt") in {blob.oid for blob in blobs}
    assert {blob.path for blob in blobs} == {"a.txt"}


def shallow_clone(repo: GitRepo, depth: int) -> GitRepo:
    clone = GitRepo(repo.path.parent / "clone", repo.home)
    repo.git("clone", "-q", "--depth", str(depth), f"file://{repo.path}", str(clone.path))
    return clone


def test_a_range_cut_by_a_shallow_clone_fails(repo: GitRepo) -> None:
    first = repo.commit_all("first")
    repo.write("a.txt", "a")
    second = repo.commit_all("second")
    repo.write("a.txt", "b")
    tip = repo.commit_all("third")
    assert repo.repository().shallow_commits() == frozenset()
    clone = shallow_clone(repo, 2).repository()
    assert clone.shallow_commits() == frozenset({second})
    assert [blob.size for blob in new_blobs(clone, RevisionRange(second, tip))] == [1]
    with pytest.raises(GitioError, match=f"cut at shallow commit {second}") as raised:
        new_blobs(clone, RevisionRange(None, tip))
    assert raised.value.diagnostic.rule == "git.revision-unresolvable"
    with pytest.raises(GitioError, match="no such commit"):
        clone.resolve_commit(first)


def test_unexpected_object_listing_fails(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.write("a.txt", "a")
    tip = repo.commit_all("first")
    git = repo.repository()
    original = Repository.git

    def fake(
        self: Repository,
        *args: str,
        stdin: bytes | None = None,
        output_limit: int = HISTORY_OUTPUT_LIMIT,
    ) -> Completed:
        if args[0] == "cat-file":
            return Completed(("git",), 0, b"garbage\n", b"", 0.0)
        return original(self, *args, stdin=stdin, output_limit=output_limit)

    monkeypatch.setattr(Repository, "git", fake)
    with pytest.raises(GitioError) as raised:
        new_blobs(git, RevisionRange(None, tip))
    assert raised.value.diagnostic.rule == "git.failed"
    assert "unexpected 'garbage'" in raised.value.diagnostic.message
