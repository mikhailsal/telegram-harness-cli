import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript.build import BuildRequest, production_build
from quality.adapters.typescript.environment import Node
from quality.testkit.nodefakes import browser_component, script

SETTINGS = """\
[components.browser]
public_env = { VITE_TITLE = "Counter" }
server_modules = ["src/server/**"]
server_packages = ["pg"]
"""
# The fake Vite records its call, then writes the report it finds in `$HOME/report`
# and a built page beside the configuration it was given.
VITE = """\
run="$(dirname "$3")"
echo "$@" > "$HOME/argv"
pwd > "$HOME/cwd"
env > "$HOME/env"
cp "$HOME/report" "$run/boundaries.json"
mkdir -p "$run/dist/assets"
echo '<script type="module" src="/assets/a.js"></script>' > "$run/dist/index.html"
echo 'x' > "$run/dist/assets/a.js"
"""


def request(snapshot: Path, run: Path, sources: frozenset[str] = frozenset()) -> BuildRequest:
    (snapshot / "web").mkdir(parents=True, exist_ok=True)
    files = ("web/src/App.tsx", "web/src/server/db.ts", "web/src/lib.ts")
    return BuildRequest(browser_component(extra=SETTINGS), snapshot, run, files, sources, "tree")


def report(home: Path, violations: list[dict[str, str]], base: str = "/") -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "report").write_text(json.dumps({"base": base, "violations": violations}))


def fake_vite(tmp_path: Path, body: str = VITE) -> Path:
    return script(tmp_path / "vite.sh", body)


def test_the_build_runs_in_the_snapshot_with_only_the_declared_values(
    tmp_path: Path, node: Node, snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("VITE_AMBIENT", "leaked")
    run = tmp_path / "run"
    report(run / "home", [])
    outcome = production_build(node, fake_vite(tmp_path), request(snapshot, run))
    assert outcome.diagnostics == []
    config = run / "vite.quality.mjs"
    assert (run / "home/argv").read_text().split() == [
        "build",
        "--config",
        str(config),
        "--mode",
        "production",
        "--logLevel",
        "warn",
    ]
    assert (run / "home/cwd").read_text().strip() == str(snapshot / "web")
    environ = (run / "home/env").read_text().splitlines()
    assert "VITE_TITLE=Counter" in environ
    assert not [line for line in environ if line.startswith(("GIT_", "VITE_AMBIENT"))]
    assert list((run / "env").iterdir()) == []
    assert str((snapshot / "web/src/server/db.ts").resolve()) in config.read_text()
    assert outcome.output is not None
    assert sorted(outcome.output.files) == ["assets/a.js", "index.html"]
    assert outcome.output.identity == "tree"


def test_the_recorded_crossings_are_reported_once(
    tmp_path: Path, node: Node, snapshot: Path
) -> None:
    run, web = tmp_path / "run", snapshot / "web"
    package = f"{web}/node_modules/leaky/index.js"
    report(
        run / "home",
        [
            {"kind": "builtin", "name": "node:fs", "importer": package},
            {"kind": "package", "name": "pg", "importer": package},
            {"kind": "module", "name": f"{web}/src/server/db.ts", "importer": f"{web}/src/lib.ts"},
            {"kind": "module", "name": f"{web}/src/server/db.ts", "importer": f"{web}/shared.ts"},
            {"kind": "builtin", "name": "fs", "importer": f"{web}/src/server/db.ts"},
        ],
    )
    sources = frozenset({"web/src/App.tsx", "web/src/lib.ts"})
    outcome = production_build(node, fake_vite(tmp_path), request(snapshot, run, sources))
    found = [(d.rule, d.location and d.location.path, d.message) for d in outcome.diagnostics]
    assert found == [
        (
            "typescript.boundaries",
            "web/node_modules/leaky/index.js",
            (
                "the production build imports the Node builtin 'node:fs', imported by "
                "web/node_modules/leaky/index.js"
            ),
        ),
        (
            "typescript.boundaries",
            "web/node_modules/leaky/index.js",
            (
                "the production build includes the server-only package 'pg', imported by "
                "web/node_modules/leaky/index.js"
            ),
        ),
        (
            "typescript.boundaries",
            "web/shared.ts",
            (
                "the production build includes the server-only module web/src/server/db.ts, "
                "imported by web/shared.ts"
            ),
        ),
    ]


def test_a_failed_build_is_a_finding(tmp_path: Path, node: Node, snapshot: Path) -> None:
    vite = fake_vite(tmp_path, "echo 'Could not resolve ./missing.ts' >&2\nexit 1\n")
    outcome = production_build(node, vite, request(snapshot, tmp_path / "run"))
    assert outcome.output is None
    (found,) = outcome.diagnostics
    assert found.rule == "typescript.build"
    assert found.message == "vite build exited with status 1: Could not resolve ./missing.ts"


def test_the_output_is_validated(tmp_path: Path, node: Node, snapshot: Path) -> None:
    run = tmp_path / "run"
    report(run / "home", [], base="/app/")
    outcome = production_build(node, fake_vite(tmp_path), request(snapshot, run))
    assert [d.message for d in outcome.diagnostics] == [
        "index.html: '/assets/a.js' is not a file of the build"
    ]


@pytest.mark.parametrize(
    ("written", "problem"),
    [
        (None, "the build wrote no usable boundary report"),
        ("[]", "boundaries.json must hold a JSON object"),
        ('{"base": "/", "violations": [{"kind": "x", "name": "y", "importer": "z"}]}', "kind 'x'"),
    ],
)
def test_an_unusable_report_is_a_tool_failure(
    tmp_path: Path, node: Node, snapshot: Path, written: str | None, problem: str
) -> None:
    run = tmp_path / "run"
    (run / "home").mkdir(parents=True)
    body = VITE
    if written is None:
        body = VITE.replace('cp "$HOME/report" "$run/boundaries.json"\n', "")
    else:
        (run / "home/report").write_text(written)
    with pytest.raises(ToolFailedError, match=problem):
        production_build(node, fake_vite(tmp_path, body), request(snapshot, run))


def test_a_build_that_cannot_run_is_a_tool_failure(tmp_path: Path, snapshot: Path) -> None:
    missing = Node(tmp_path / "missing-node", tmp_path / "cache")
    with pytest.raises(ToolFailedError, match="vite"):
        production_build(missing, tmp_path / "vite.sh", request(snapshot, tmp_path / "run"))
