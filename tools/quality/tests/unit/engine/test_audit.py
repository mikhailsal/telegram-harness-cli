from datetime import date
from pathlib import Path

import pytest

from quality.adapters.osv import Advisory
from quality.catalog import build_registry
from quality.engine.audit import AuditReport, AuditSettings, audit_candidate, severity
from quality.gitio import staged
from quality.policy import AuditPolicy
from quality.testkit.gitrepo import GitRepo
from quality.testkit.osvdb import MEDIUM, advisory_db, pinned_scanner, uv_lock
from quality.testkit.osvdb import Advisory as Synthetic
from quality.testkit.policies import ComponentSpec, classification_toml, policy_toml

TODAY = date(2026, 10, 21)
EXCEPTION = """\
[[exceptions]]
rule = "dependencies.vulnerable"
path = "uv.lock"
symbol = "TEST-1"
reason = "The vulnerable template path is not reachable from the demo."
owner = "fixtures"
expires = 2026-12-31
"""


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return advisory_db(
        tmp_path / "db",
        Synthetic("TEST-1", "jinja2", "2.10.1", aliases=("CVE-2099-1",)),
        Synthetic("TEST-2", "idna", "3.0", vector=MEDIUM),
        Synthetic("TEST-3", "six", "2.0", vector=None),
    )


def project(tmp_path: Path, lock: str, *, extra: str = "") -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml(ComponentSpec(), extra=extra))
    repo.write("pyproject.toml", '[project]\nname = "demo"\nversion = "0.1.0"\n')
    repo.write("uv.lock", lock)
    repo.write("src/app.py", "x = 1\n")
    repo.git("add", "-A")
    return repo


def audit(repo: GitRepo, database: Path | None, scanner: Path | None = None) -> AuditReport:
    git = repo.repository()
    settings = AuditSettings(
        scanner=scanner or pinned_scanner(),
        rules={rule.id: rule for rule in build_registry()},
        today=TODAY,
        database=database,
    )
    return audit_candidate(git, staged(git), settings)


def found(report: AuditReport) -> list[tuple[str, str, str]]:
    return [(d.rule, str(d.severity), d.message) for d in report.diagnostics]


def test_a_clean_lockfile_passes(tmp_path: Path, database: Path) -> None:
    report = audit(project(tmp_path, uv_lock(jinja2="2.10.1")), database)
    assert report == AuditReport((), ("uv.lock",), 0)


def test_only_lockfiles_classified_as_configuration_are_scanned(
    tmp_path: Path, database: Path
) -> None:
    vendored = classification_toml("vendor/**", "vendor")
    repo = project(tmp_path, uv_lock(jinja2="2.10.1"), extra=vendored)
    repo.write("vendor/lib/uv.lock", uv_lock(jinja2="2.10"))
    repo.write("docs/uv.lock.md", "# Lock notes\n")
    repo.git("add", "-A")
    assert audit(repo, database) == AuditReport((), ("uv.lock",), 0)


def test_advisories_are_graded_by_the_audit_policy(tmp_path: Path, database: Path) -> None:
    lock = uv_lock(jinja2="2.10", idna="2.8", six="1.0")
    report = audit(project(tmp_path, lock), database)
    assert found(report) == [
        ("dependencies.vulnerable", "warning", "idna 2.8 (PyPI) is affected by TEST-2 (CVSS 5.4)"),
        (
            "dependencies.vulnerable",
            "error",
            "jinja2 2.10 (PyPI) is affected by TEST-1, CVE-2099-1 (CVSS 9.8)",
        ),
        (
            "dependencies.vulnerable",
            "error",
            "six 1.0 (PyPI) is affected by TEST-3 (no CVSS score)",
        ),
    ]
    assert [d.data["symbol"] for d in report.diagnostics] == ["TEST-2", "TEST-1", "TEST-3"]
    assert {d.location.path for d in report.diagnostics if d.location} == {"uv.lock"}


def test_policy_sets_the_blocking_score_and_unscored_advisories() -> None:
    lenient = AuditPolicy(blocking_score=9.9, unscored="warning")
    scored = Advisory("a", "1", "PyPI", ("X",), (), 9.8)
    unscored = Advisory("a", "1", "PyPI", ("X",), (), None)
    assert (severity(scored, lenient), severity(unscored, lenient)) == ("warning", "warning")
    assert severity(scored, AuditPolicy()) == "error"


def test_an_exception_covers_exactly_one_advisory(tmp_path: Path, database: Path) -> None:
    lock = uv_lock(jinja2="2.10", six="1.0")
    report = audit(project(tmp_path, lock, extra=EXCEPTION), database)
    assert [d.data["symbol"] for d in report.diagnostics] == ["TEST-3"]
    assert report.excepted == 1


def test_a_missing_database_fails_visibly(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    report = audit(project(tmp_path, uv_lock(jinja2="2.10.1")), tmp_path / "empty")
    [diagnostic] = report.diagnostics
    assert (diagnostic.rule, diagnostic.kind) == ("dependencies.scanner", "environment")
    assert "osv-scanner: exit status 127" in diagnostic.message


def test_a_missing_scanner_fails_visibly(tmp_path: Path, database: Path) -> None:
    repo = project(tmp_path, uv_lock(jinja2="2.10.1"))
    report = audit(repo, database, scanner=tmp_path / "absent")
    assert [d.rule for d in report.diagnostics] == ["dependencies.scanner"]


def test_an_invalid_policy_stops_the_audit(tmp_path: Path, database: Path) -> None:
    repo = project(tmp_path, uv_lock(jinja2="2.10"))
    repo.write("quality.toml", "schema_version = 1\n")
    repo.git("add", "-A")
    report = audit(repo, database)
    assert report.lockfiles == ()
    assert {d.kind for d in report.diagnostics} == {"usage"}
