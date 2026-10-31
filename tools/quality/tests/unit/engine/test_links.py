from datetime import date
from pathlib import Path

import pytest

from quality.adapters.web import Probe
from quality.catalog import build_registry
from quality.diagnostics import Location
from quality.engine import Report, check_links
from quality.gitio import staged
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml

TODAY = date(2026, 10, 20)
STATUS = {
    "https://example.org/ok": Probe(200),
    "https://example.org/gone": Probe(404),
    "https://slow.test/": Probe(None, "TimeoutError: timed out"),
}


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("README.md", "# P\n\n[ok](https://example.org/ok) [slow](https://slow.test/)\n")
    repo.write("docs/a.md", "# A\n\n[gone](https://example.org/gone#x)\n")
    repo.write("src/app/__init__.py", "# [not docs](https://example.org/gone)\n")
    repo.write("docs/latin1.md", b"[x](https://example.org/gone) \xff\n")
    repo.write("tests/test_app.py", "")
    repo.git("add", "-A")
    return repo


def run(repo: GitRepo) -> tuple[Report, list[str]]:
    probed: list[str] = []

    def prober(url: str) -> Probe:
        probed.append(url)
        return STATUS[url]

    git = repo.repository()
    rules = {rule.id: rule for rule in build_registry()}
    report = check_links(git, staged(git), rules=rules, prober=prober, today=TODAY)
    return report, sorted(probed)


def test_each_external_link_is_probed_once(project: GitRepo) -> None:
    report, probed = run(project)
    assert probed == sorted(STATUS)
    assert sorted((d.rule, d.location) for d in report.diagnostics) == [
        ("links.external-broken", Location("docs/a.md", 3)),
        ("links.external-unverified", Location("README.md", 3)),
    ]
    assert (report.files, report.excepted) == (2, 0)


def test_link_exceptions_apply_and_other_exceptions_are_ignored(project: GitRepo) -> None:
    extra = """
[[exceptions]]
rule = "links.external-broken"
path = "docs/a.md"
reason = "archived page, kept for history"
owner = "docs"
expires = 2026-12-31

[[exceptions]]
rule = "structure.complexity"
path = "src/app/__init__.py"
reason = "not a links rule"
owner = "docs"
expires = 2026-12-31
"""
    project.write("quality.toml", policy_toml(extra=extra))
    project.git("add", "quality.toml")
    report, _ = run(project)
    assert [d.rule for d in report.diagnostics] == ["links.external-unverified"]
    assert report.excepted == 1


def test_an_invalid_policy_stops_before_probing(project: GitRepo) -> None:
    project.git("rm", "-q", "--cached", "quality.toml")
    report, probed = run(project)
    assert [d.rule for d in report.diagnostics] == ["policy.missing"]
    assert probed == []
