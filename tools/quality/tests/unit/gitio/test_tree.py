import hashlib

import pytest

from quality.gitio import GitioError, blob_id, list_tree, read_blobs, read_path
from quality.gitio import tree as tree_module
from quality.gitio.tree import blob_sizes
from quality.testkit.gitrepo import GitRepo

UNUSUAL = ["with space.txt", "new\nline.txt", "tab\there.txt", "ünïcode.txt", "-dash.txt"]


def test_list_tree_reports_modes_sizes_and_unusual_paths(repo: GitRepo) -> None:
    for name in UNUSUAL:
        repo.write(name, name)
    repo.write(b"bad-\xff.txt", "raw bytes")
    repo.write("bin/tool", "#!/bin/sh\n", executable=True)
    (repo.path / "link").symlink_to("bin/tool")
    repo.commit_all("files")
    entries = {e.path: e for e in list_tree(repo.repository(), "HEAD")}
    assert set(UNUSUAL) <= set(entries)
    assert "bad-\udcff.txt" in entries
    assert entries["bin/tool"].is_executable
    assert entries["bin/tool"].is_file
    assert entries["link"].is_symlink
    assert not entries["link"].is_file
    assert entries["with space.txt"].size == len("with space.txt")


def test_list_tree_reports_gitlinks(repo: GitRepo) -> None:
    commit = repo.commit_all("root")
    repo.git("update-index", "--add", "--cacheinfo", f"160000,{commit},vendor/lib")
    repo.git("commit", "-q", "-m", "submodule")
    (entry,) = [e for e in list_tree(repo.repository(), "HEAD") if e.is_gitlink]
    assert (entry.path, entry.oid, entry.size) == ("vendor/lib", commit, 0)


def test_read_blobs_deduplicates_and_chunks(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tree_module, "CHUNK_BYTES", 8)
    for index in range(5):
        repo.write(f"f{index}.txt", f"content {index}\n")
    repo.write("copy.txt", "content 0\n")
    repo.commit_all("files")
    git = repo.repository()
    entries = list_tree(git, "HEAD")
    blobs = dict(read_blobs(git, ((e.oid, e.size) for e in entries)))
    assert len(blobs) == 5
    assert sorted(blobs.values()) == [f"content {i}\n".encode() for i in range(5)]


def test_read_blobs_rejects_missing_objects(repo: GitRepo) -> None:
    with pytest.raises(GitioError, match="unexpected object header"):
        list(read_blobs(repo.repository(), [("f" * 40, 1)]))


def test_blob_sizes_reject_missing_objects(repo: GitRepo) -> None:
    git = repo.repository()
    assert blob_sizes(git, []) == {}
    with pytest.raises(GitioError, match="missing"):
        blob_sizes(git, ["f" * 40])


def test_blob_ids_match_git(repo: GitRepo) -> None:
    for data in (b"", b"hello\n", bytes(range(256))):
        assert blob_id(data) == repo.git("hash-object", "--stdin", stdin=data).strip()
    expected = hashlib.sha256(b"blob 3\0abc").hexdigest()
    assert blob_id(b"abc", algorithm="sha256") == expected


def test_read_path_returns_one_regular_file_within_the_limit(repo: GitRepo) -> None:
    repo.write("policy.toml", "a = 1\n")
    repo.write("dir/file", "x")
    (repo.path / "link").symlink_to("policy.toml")
    repo.commit_all("files")
    git = repo.repository()
    tree = git.tree_of(git.resolve_commit("HEAD"))
    assert read_path(git, tree, "policy.toml", 6) == b"a = 1\n"
    assert read_path(git, tree, "policy.toml", 5) is None
    assert read_path(git, tree, "link", 100) is None
    assert read_path(git, tree, "dir", 100) is None
    assert read_path(git, tree, "absent", 100) is None
