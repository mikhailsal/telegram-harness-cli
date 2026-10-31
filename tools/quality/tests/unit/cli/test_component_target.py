"""`component add --target`: React + Vite evidence, ambiguity, and refusals (decision 0017)."""

import json
from dataclasses import dataclass

import pytest

from quality.cli.main import main
from quality.policy import Bundler, Capability, Component, ExecutionTarget, Framework, load_policy
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml

POLICY = policy_toml(ComponentSpec(root="app"))


@pytest.fixture
def project(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo.write("quality.toml", POLICY)
    repo.write("app/src/app/__init__.py", "")
    repo.commit_all("base")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


STACK = {
    "dependencies": {"react": "19.3.1", "react-dom": "19.3.1"},
    "devDependencies": {"vite": "8.3.5", "@vitejs/plugin-react": "6.1.2"},
}
APP_FILES = ("vite.config.ts", "index.html")


def react_app(
    repo: GitRepo, root: str, manifest: object = STACK, files: tuple[str, ...] = APP_FILES
) -> None:
    repo.write(f"{root}/package.json", json.dumps(manifest))
    for name in (*files, "src/.keep", "tests/.keep"):
        repo.write(f"{root}/{name}", "// user file\n")


def last(repo: GitRepo) -> Component:
    policy = load_policy(repo.path).policy
    assert policy is not None
    return policy.components[-1]


def test_a_react_vite_application_registers_as_a_browser_component(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    react_app(project, "apps/web")
    assert main(["component", "add", "apps/web", "--target", "browser"]) == 0
    web = last(project)
    assert (web.id, web.target, web.framework, web.bundler, web.capabilities) == (
        "web",
        ExecutionTarget.BROWSER,
        Framework.REACT,
        Bundler.VITE,
        [Capability.UI],
    )
    out = capsys.readouterr().out
    assert (
        'profile = "typescript"\ntarget = "browser"\nframework = "react"\nbundler = "vite"\n' in out
    )
    assert (project.path / "apps/web/vite.config.ts").read_text() == "// user file\n"
    assert json.loads((project.path / "apps/web/package.json").read_text()) == STACK


def test_the_ui_capability_comes_first_and_is_not_repeated(project: GitRepo) -> None:
    react_app(project, "web")
    arguments = ["--capability", "http-api", "--capability", "ui"]
    assert main(["component", "add", "web", "--target", "browser", *arguments]) == 0
    assert last(project).capabilities == [Capability.HTTP_API, Capability.UI]
    react_app(project, "admin")
    assert (
        main(["component", "add", "admin", "--target", "browser", "--capability", "http-api"]) == 0
    )
    assert last(project).capabilities == [Capability.UI, Capability.HTTP_API]


def test_an_explicit_node_target_is_recorded_whatever_the_evidence(project: GitRepo) -> None:
    react_app(project, "ssr")
    assert main(["component", "add", "ssr", "--target", "node", "--capability", "http-api"]) == 0
    node = last(project)
    assert (node.target, node.framework, node.in_browser) == (ExecutionTarget.NODE, None, False)


@dataclass(frozen=True)
class Refusal:
    """A project, the target named for it, and the start of the refusal."""

    manifest: object
    files: tuple[str, ...]
    target: tuple[str, ...]
    message: str


BROWSER = ("--target", "browser")


@pytest.mark.parametrize(
    "case",
    [
        Refusal(
            STACK,
            APP_FILES,
            (),
            "found react, react-dom, vite, @vitejs/plugin-react, vite.config.ts, index.html, "
            "so the target is ambiguous; name it with --target browser or --target node",
        ),
        Refusal({"devDependencies": {"vite": "8"}}, (), (), "found vite, so the target is"),
        Refusal({"dependencies": {"vue": "3"}}, (), (), "found Vue, so the target is ambiguous"),
        Refusal(
            {"dependencies": {"react": "19.3.1"}},
            ("index.html",),
            BROWSER,
            "a React + Vite application needs the react-dom package, the vite package, the "
            "@vitejs/plugin-react package, a vite.config.* file",
        ),
        Refusal(
            {"dependencies": {"react": "19.3.1"}},
            ("vite.config.ts",),
            BROWSER,
            "a React + Vite application needs the react-dom package, the vite package, the "
            "@vitejs/plugin-react package, an index.html entry point",
        ),
        Refusal(
            {**STACK, "optionalDependencies": {"next": "16.0.0"}},
            APP_FILES,
            BROWSER,
            "Next.js is not supported; the browser target supports React with Vite",
        ),
        Refusal("{", APP_FILES, BROWSER, "package.json is not valid JSON"),
    ],
)
def test_a_target_that_the_evidence_does_not_support_is_refused(
    project: GitRepo, capsys: pytest.CaptureFixture[str], case: Refusal
) -> None:
    react_app(project, "web", case.manifest, case.files)
    if isinstance(case.manifest, str):
        project.write("web/package.json", case.manifest)
    assert main(["component", "add", "web", *case.target, "--capability", "cli"]) == 2
    assert capsys.readouterr().err.startswith(f"component add: {case.message}")
    assert (project.path / "quality.toml").read_text() == POLICY


def test_a_target_applies_only_to_typescript(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("svc/composer.json", "{}\n")
    for directory in ("src", "tests"):
        project.write(f"svc/{directory}/.keep", "")
    assert main(["component", "add", "svc", "--target", "node", "--capability", "cli"]) == 2
    assert (
        capsys.readouterr().err
        == "component add: --target applies only to the typescript profile\n"
    )


def test_a_browser_application_registers_its_scenarios_when_they_exist(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    react_app(project, "web")
    assert main(["component", "add", "web", "--target", "browser"]) == 0
    web = last(project)
    assert (web.tests.e2e, web.scenarios) == ([], None)
    react_app(project, "admin", files=(*APP_FILES, "e2e/.keep", "scenarios.toml"))
    capsys.readouterr()
    assert main(["component", "add", "admin", "--target", "browser"]) == 0
    admin = last(project)
    assert (admin.tests.e2e, admin.scenarios) == (["e2e"], "scenarios.toml")
    out = capsys.readouterr().out
    assert 'tests = { unit = ["tests"], e2e = ["e2e"] }\n' in out
    assert 'capabilities = ["ui"]\nscenarios = "scenarios.toml"\n' in out


def test_any_component_can_name_its_end_to_end_suites_and_manifest(project: GitRepo) -> None:
    for root in ("svc", "api"):
        project.write(f"{root}/pyproject.toml", f'[project]\nname = "{root}"\n')
        for name in ("src/.keep", "tests/.keep", "e2e/.keep", "scenarios.toml", "s.toml"):
            project.write(f"{root}/{name}", "")
    assert main(["component", "add", "svc", "--capability", "cli"]) == 0
    assert (last(project).tests.e2e, last(project).scenarios) == ([], None)
    named = ["--capability", "cli", "--e2e", "e2e", "--scenarios", "s.toml"]
    assert main(["component", "add", "api", *named]) == 0
    assert (last(project).tests.e2e, last(project).scenarios) == (["e2e"], "s.toml")
