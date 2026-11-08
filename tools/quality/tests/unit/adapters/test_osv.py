import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.osv import Advisory, scan_lockfiles, score
from quality.testkit.nodefakes import script
from quality.testkit.osvdb import MEDIUM, advisory_db, pinned_scanner, uv_lock
from quality.testkit.osvdb import Advisory as Synthetic


@pytest.fixture(scope="module")
def scanner() -> Path:
    return pinned_scanner()


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return advisory_db(
        tmp_path / "db",
        Synthetic("TEST-1", "jinja2", "2.10.1", aliases=("CVE-2099-1",)),
        Synthetic("TEST-2", "idna", "3.0", vector=MEDIUM),
        Synthetic("TEST-3", "six", "2.0", vector=None),
    )


def lockfile(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "uv.lock"
    path.write_text(text)
    return path


def test_no_lockfiles_need_no_scan(tmp_path: Path) -> None:
    assert scan_lockfiles([], tmp_path, scanner=tmp_path / "absent", database=None) == {}


def test_advisories_are_reported_by_lockfile_with_their_highest_score(
    tmp_path: Path, scanner: Path, database: Path
) -> None:
    clean = lockfile(tmp_path / "a", uv_lock(attrs="20.1"))
    vulnerable = lockfile(tmp_path / "b", uv_lock(jinja2="2.10", idna="2.8", six="1.0"))
    work = tmp_path / "work"
    found = scan_lockfiles([clean, vulnerable], work, scanner=scanner, database=database)
    assert found == {
        1: [
            Advisory("idna", "2.8", "PyPI", ("TEST-2",), ("TEST-2",), 5.4),
            Advisory("jinja2", "2.10", "PyPI", ("TEST-1",), ("CVE-2099-1", "TEST-1"), 9.8),
            Advisory("six", "1.0", "PyPI", ("TEST-3",), ("TEST-3",), None),
        ]
    }
    assert (work / "1" / "uv.lock").read_text() == vulnerable.read_text()


def test_fixed_versions_are_clean(tmp_path: Path, scanner: Path, database: Path) -> None:
    fixed = lockfile(tmp_path / "a", uv_lock(jinja2="2.10.1", idna="3.0"))
    assert scan_lockfiles([fixed], tmp_path / "work", scanner=scanner, database=database) == {}


def test_a_lockfile_without_packages_is_clean(
    tmp_path: Path, scanner: Path, database: Path
) -> None:
    empty = lockfile(tmp_path / "a", uv_lock())
    assert scan_lockfiles([empty], tmp_path / "work", scanner=scanner, database=database) == {}


def test_a_missing_database_is_a_scanner_failure(tmp_path: Path, scanner: Path) -> None:
    locked = lockfile(tmp_path / "a", uv_lock(jinja2="2.10"))
    (tmp_path / "db").mkdir()
    with pytest.raises(ToolFailedError, match="exit status 127"):
        scan_lockfiles([locked], tmp_path / "work", scanner=scanner, database=tmp_path / "db")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("9.8", 9.8), ("0", 0.0), ("10.0", 10.0), ("", None), ("10.1", None), ("high", None)],
)
def test_scores_are_cvss_base_scores(text: str, expected: float | None) -> None:
    assert score(text) == expected


def group(**overrides: object) -> dict[str, object]:
    return {"ids": ["GHSA-1"], "aliases": ["CVE-1"], "max_severity": "7.5", **overrides}


def report(work: Path, *groups: object, path: str | None = None) -> dict[str, object]:
    package = {"name": "jinja2", "version": "2.10", "ecosystem": "PyPI"}
    source = {"path": path or str(work / "0" / "uv.lock"), "type": "lockfile"}
    packages = [{"package": package, "groups": list(groups)}]
    return {"results": [{"source": source, "packages": packages}]}


def fake(tmp_path: Path, status: int, document: object) -> Path:
    body = f"cat <<'EOF'\n{json.dumps(document)}\nEOF\nexit {status}\n"
    return script(tmp_path / "fake-osv", body)


def scan_with(tmp_path: Path, status: int, document: object) -> dict[int, list[Advisory]]:
    locked = lockfile(tmp_path / "a", uv_lock(jinja2="2.10"))
    fake_scanner = fake(tmp_path, status, document)
    return scan_lockfiles([locked], tmp_path / "work", scanner=fake_scanner, database=None)


def test_the_online_mode_reads_the_api_report(tmp_path: Path) -> None:
    found = scan_with(tmp_path, 1, report(tmp_path / "work", group()))
    assert found == {0: [Advisory("jinja2", "2.10", "PyPI", ("GHSA-1",), ("CVE-1",), 7.5)]}


@pytest.mark.parametrize(
    ("status", "document", "message"),
    [
        (0, "advisories", "exit status and report disagree"),
        (1, "clean", "exit status and report disagree"),
        (1, "unknown", "result for an unknown lockfile: /elsewhere/uv.lock"),
        (1, "no-ids", "advisory group without IDs"),
        (1, "bad-ids", "report field ids holds a non-text entry"),
        (1, "no-severity", "report entry without text field max_severity"),
        (1, "no-results", "results is not a list"),
        (1, "not-object", "report is not an object"),
        (2, "clean", "exit status 2"),
    ],
)
def test_unusable_reports_fail_the_scan(
    tmp_path: Path, status: int, document: str, message: str
) -> None:
    work = tmp_path / "work"
    documents: dict[str, object] = {
        "advisories": report(work, group()),
        "clean": {"results": []},
        "unknown": report(work, group(), path="/elsewhere/uv.lock"),
        "no-ids": report(work, group(ids=[])),
        "bad-ids": report(work, group(ids=[1])),
        "no-severity": report(work, group(max_severity=None)),
        "no-results": {"results": None},
        "not-object": [],
    }
    with pytest.raises(ToolFailedError, match=message):
        scan_with(tmp_path, status, documents[document])


def test_unparsable_and_truncated_reports_fail_the_scan(tmp_path: Path) -> None:
    locked = lockfile(tmp_path / "a", uv_lock(jinja2="2.10"))
    broken = script(tmp_path / "broken", "echo '{'\nexit 1\n")
    with pytest.raises(ToolFailedError, match="osv-scanner report"):
        scan_lockfiles([locked], tmp_path / "w1", scanner=broken, database=None)
    flood = script(tmp_path / "flood", "head -c 17000000 /dev/zero\nexit 1\n")
    with pytest.raises(ToolFailedError, match="output limit"):
        scan_lockfiles([locked], tmp_path / "w2", scanner=flood, database=None)
