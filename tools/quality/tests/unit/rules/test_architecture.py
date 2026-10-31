from quality.rules.architecture import check_architecture, strongly_connected
from quality.testkit.policies import ComponentSpec, build_policy

ARCHITECTURE = """
[components.architecture]
layers = ["cli", "core", "base"]

[[components.architecture.modules]]
name = "cli"
paths = ["src/app/cli/**"]

[[components.architecture.modules]]
name = "core"
paths = ["src/app/core/**", "src/app/shared/*.py"]

[[components.architecture.modules]]
name = "base"
paths = ["src/app/base/**"]

[[components.architecture.modules]]
name = "plugins"
paths = ["src/app/plugins/**", "src/app/shared/both.py"]

[[components.architecture.forbidden]]
from = "plugins"
to = "cli"
"""

FILES = [
    "src/app/cli/main.py",
    "src/app/core/model.py",
    "src/app/base/io.py",
    "src/app/plugins/x.py",
    "src/app/free.py",
]
AMBIGUOUS = "src/app/shared/both.py"


def run(
    edges: dict[str, dict[str, int]], root: str = ".", files: tuple[str, ...] = ()
) -> list[tuple[str, str, str]]:
    spec = ComponentSpec(root=root, extra=ARCHITECTURE)
    component = build_policy(spec).components[0]
    prefix = "" if root == "." else root + "/"
    paths = [prefix + path for path in [*FILES, *files]]
    prefixed = {
        prefix + source: {prefix + target: line for target, line in targets.items()}
        for source, targets in edges.items()
    }
    return [
        (d.rule, d.message, d.location.render() if d.location else "")
        for d in check_architecture(component, paths, prefixed)
    ]


def test_downward_and_undeclared_dependencies_pass() -> None:
    edges = {
        "src/app/cli/main.py": {"src/app/core/model.py": 3, "src/app/free.py": 4},
        "src/app/core/model.py": {"src/app/base/io.py": 1, "src/app/plugins/x.py": 2},
        "src/app/free.py": {"src/app/cli/main.py": 1},
    }
    assert run(edges) == []


def test_upward_layer_dependencies_fail_at_the_first_import() -> None:
    edges = {"src/app/base/io.py": {"src/app/cli/main.py": 9, "src/app/core/model.py": 4}}
    assert run(edges) == [
        (
            "architecture.forbidden-dependency",
            "layer 'base' must not depend on the higher layer 'cli'",
            "src/app/base/io.py:9",
        ),
        (
            "architecture.forbidden-dependency",
            "layer 'base' must not depend on the higher layer 'core'",
            "src/app/base/io.py:4",
        ),
    ]


def test_forbidden_edges_and_cycles_fail() -> None:
    edges = {
        "src/app/plugins/x.py": {"src/app/cli/main.py": 2},
        "src/app/cli/main.py": {"src/app/plugins/x.py": 5},
    }
    assert run(edges, root="apps/x", files=(AMBIGUOUS,)) == [
        (
            "architecture.ambiguous-module",
            "file matches several declared modules",
            "apps/x/src/app/shared/both.py",
        ),
        (
            "architecture.forbidden-dependency",
            "module 'plugins' must not depend on 'cli'",
            "apps/x/src/app/plugins/x.py:2",
        ),
        (
            "architecture.cycle",
            "modules depend on each other: cli <-> plugins",
            "apps/x/src/app/cli/main.py:5",
        ),
    ]


def test_ambiguous_files_are_not_assigned_to_any_module() -> None:
    edges = {AMBIGUOUS: {"src/app/cli/main.py": 1}}
    assert [rule for rule, _, _ in run(edges, files=(AMBIGUOUS,))] == [
        "architecture.ambiguous-module"
    ]


def test_evidence_is_the_first_import_of_the_first_file() -> None:
    edges = {
        "src/app/base/io.py": {"src/app/free.py": 1},
        "src/app/base/z.py": {"src/app/core/model.py": 1},
        "src/app/base/a.py": {"src/app/core/model.py": 7, "src/app/cli/main.py": 7},
    }
    files = ("src/app/base/z.py", "src/app/base/a.py")
    locations = [location for _, _, location in run(edges, files=files)]
    assert locations == ["src/app/base/a.py:7", "src/app/base/a.py:7"]


def test_strongly_connected_components() -> None:
    graph = {"a": ["b"], "b": ["c", "a"], "c": ["c"], "d": ["e"], "e": ["f"], "f": ["d"]}
    assert strongly_connected(["a", "b", "c", "d", "e", "f", "g"], graph) == [
        ["a", "b"],
        ["d", "e", "f"],
    ]
