import pytest

from quality.discovery import affected_components
from quality.policy import Policy
from quality.testkit.policies import ComponentSpec, build_policy

LIB_A = ComponentSpec(id="a", root="libs/a")
LIB_B = ComponentSpec(id="b", root="libs/b", extra='depends_on = ["a"]')
APP_C = ComponentSpec(id="c", root="apps/c", extra='depends_on = ["b"]')
APP_D = ComponentSpec(id="d", root="apps/d")
POLICY = build_policy(LIB_A, LIB_B, APP_C, APP_D)
EVERY = ["a", "b", "c", "d"]


def affected(changed: list[str], policy: Policy = POLICY) -> list[str]:
    return [component.id for component in affected_components(policy, changed)]


def test_no_change_affects_nothing() -> None:
    assert affected([]) == []


@pytest.mark.parametrize(
    "path",
    [
        "apps/d/src/app/main.py",
        "apps/d/tests/test_main.py",
        "apps/d/pyproject.toml",
        "apps/d/uv.lock",
        "apps/d/README.md",
    ],
)
def test_a_change_inside_a_component_affects_only_it(path: str) -> None:
    assert affected([path]) == ["d"]


def test_a_deleted_test_affects_its_component() -> None:
    # Deletions are reported under their old path.
    assert affected(["apps/d/tests/test_removed.py"]) == ["d"]


def test_dependents_are_affected_transitively() -> None:
    assert affected(["libs/a/src/a/core.py"]) == ["a", "b", "c"]
    assert affected(["libs/b/tests/test_b.py"]) == ["b", "c"]
    assert affected(["apps/c/src/c/cli.py"]) == ["c"]


def test_results_follow_declaration_order() -> None:
    assert affected(["apps/d/src/x.py", "apps/c/src/y.py", "libs/b/src/z.py"]) == ["b", "c", "d"]


@pytest.mark.parametrize(
    "path",
    [
        "quality.toml",
        "tools/quality/src/quality/engine/suites.py",
        "tools/quality/toolchains.toml",
        "tools/quality/uv.lock",
        "tools/quality/README.md",
    ],
)
def test_policy_and_core_changes_affect_every_component(path: str) -> None:
    assert affected(["apps/d/src/x.py", path]) == EVERY


@pytest.mark.parametrize(
    "path",
    [".github/workflows/ci.yml", "Makefile", "package-lock.json", "scripts/build.sh", ".gitignore"],
)
def test_unowned_non_documentation_changes_affect_every_component(path: str) -> None:
    assert affected([path]) == EVERY


@pytest.mark.parametrize(
    "path", ["README.md", "docs/guide.md", "docs/decisions/0001-x.md", "tools/quality-notes.md"]
)
def test_unowned_documentation_affects_nothing(path: str) -> None:
    assert affected([path]) == []


def test_the_core_root_is_matched_as_a_directory() -> None:
    assert affected(["tools/quality-extras/notes.md"]) == []
    assert affected(["tools/quality-extras/run.py"]) == EVERY


def test_nested_roots_resolve_to_the_most_specific_component() -> None:
    policy = build_policy(
        ComponentSpec(id="outer", root="apps"), ComponentSpec(id="inner", root="apps/api")
    )
    assert affected(["apps/api/src/m.py"], policy) == ["inner"]
    assert affected(["apps/web/src/m.py"], policy) == ["outer"]


def test_a_root_component_owns_every_path() -> None:
    policy = build_policy(ComponentSpec(id="app", root="."), LIB_A)
    assert affected(["Makefile"], policy) == ["app"]
    assert affected(["libs/a/src/a.py"], policy) == ["a"]


def test_dependency_cycles_terminate() -> None:
    policy = build_policy(
        ComponentSpec(id="x", root="x", extra='depends_on = ["y"]'),
        ComponentSpec(id="y", root="y", extra='depends_on = ["x"]'),
        ComponentSpec(id="z", root="z"),
    )
    assert affected(["x/src/m.py"], policy) == ["x", "y"]
