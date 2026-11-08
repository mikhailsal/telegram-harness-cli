import json
from pathlib import Path

import pytest

from quality.adapters import web
from quality.adapters.web import Probe
from quality.cli.main import main
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml


@pytest.fixture
def probed(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    seen: dict[str, float] = {}

    def fake(url: str, *, timeout: float) -> Probe:
        seen[url] = timeout
        return Probe(404) if "gone" in url else Probe(200)

    monkeypatch.setattr(web, "probe", fake)
    return seen


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("README.md", "# P\n\n[ok](https://example.org/)\n")
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_app.py", "")
    repo.git("add", "-A")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


def test_working_links_pass(
    project: GitRepo, probed: dict[str, float], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["links"]) == 0
    assert probed == {"https://example.org/": 10.0}
    assert capsys.readouterr().out == "PASSED: 0 error(s), 0 warning(s)\nchecked links in 1 pages\n"


def test_gone_links_fail_with_a_json_report(
    project: GitRepo, probed: dict[str, float], capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("README.md", "# P\n\n[gone](https://example.org/gone)\n")
    project.git("add", "-A")
    assert main(["links", "--json", "--timeout", "2.5"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert (report["command"], report["pages"], report["excepted"]) == ("links", 1, 0)
    assert [d["rule"] for d in report["diagnostics"]] == ["links.external-broken"]
    assert probed == {"https://example.org/gone": 2.5}


def test_a_revision_is_checked(project: GitRepo, probed: dict[str, float]) -> None:
    project.git("commit", "-q", "-m", "first")
    project.write("README.md", "# P\n\n[gone](https://example.org/gone)\n")
    project.git("add", "-A")
    assert main(["links", "--rev", "HEAD"]) == 0
    assert list(probed) == ["https://example.org/"]


@pytest.mark.parametrize("timeout", ["0", "-1"])
def test_the_timeout_must_be_positive(
    project: GitRepo, timeout: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["links", "--timeout", timeout]) == 2
    assert capsys.readouterr().err == "quality links: --timeout must be positive\n"


def test_outside_a_repository_is_an_environment_error(
    project: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert main(["links"]) == 3
    assert "(git.failed)" in capsys.readouterr().out
