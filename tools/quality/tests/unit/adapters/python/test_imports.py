import pytest

from quality.adapters.python.imports import file_dependencies, import_roots, module_of, targets
from quality.adapters.python.structure import PythonStructure
from quality.metrics import ImportRef


def test_import_roots_follow_src_and_flat_layouts() -> None:
    files = {"app/pkg/__init__.py", "app/src/lib/__init__.py"}
    assert import_roots(["app/src", "app/pkg"], files) == ["app/src", "app"]
    assert import_roots(["pkg"], {"pkg/__init__.py"}) == [""]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/quality/cli/main.py", ("quality.cli.main", False)),
        ("src/quality/__init__.py", ("quality", True)),
        ("src/quality/stubs.pyi", ("quality.stubs", False)),
        ("src/quality/data.json", None),
        ("src/my-pkg/x.py", None),
        ("other/x.py", None),
        ("src/__init__.py", None),
    ],
)
def test_module_names(path: str, expected: tuple[str, bool] | None) -> None:
    assert module_of(path, ["src"]) == expected


def test_module_names_at_the_repository_root() -> None:
    assert module_of("pkg/a.py", [""]) == ("pkg.a", False)


@pytest.mark.parametrize(
    ("ref", "package", "expected"),
    [
        (ImportRef("os.path", 1), False, ["os.path"]),
        (ImportRef("a", 1, 0, ("x", "y")), False, ["a.x", "a.y"]),
        (ImportRef("", 1, 1, ("x",)), False, ["a.b.x"]),
        (ImportRef("", 1, 1, ("x",)), True, ["a.b.c.x"]),
        (ImportRef("up", 1, 2, ()), False, ["a.up"]),
        (ImportRef("", 1, 2, ("x",)), False, ["a.x"]),
        (ImportRef("", 1, 3, ("x",)), False, []),
    ],
    ids=["absolute", "from", "sibling", "package", "parent", "top", "escapes"],
)
def test_import_targets(ref: ImportRef, package: bool, expected: list[str]) -> None:
    assert targets(ref, "a.b.c", package=package) == expected


def test_relative_import_in_a_top_level_module_escapes() -> None:
    assert targets(ImportRef("", 1, 1, ("x",)), "top", package=False) == []


def test_file_dependencies_resolve_to_the_most_specific_file() -> None:
    sources = {
        "src/app/__init__.py": b"",
        "src/app/core.py": b"from app import util\nimport app.web.views\nimport os\n",
        "src/app/util.py": b"from . import core\nfrom .core import thing\n",
        "src/app/web/__init__.py": b"from ..util import helper\nfrom .. import missing\n",
        "src/app/web/views.py": b"from .. import core\n",
        "src/app/data.txt": b"",
    }
    analyzer = PythonStructure()
    metrics = {p: analyzer.analyze(p, s) for p, s in sources.items() if p.endswith(".py")}
    metrics["src/app/data.txt"] = analyzer.analyze("src/app/data.txt", b"import app\n")
    assert file_dependencies(metrics, ["src"]) == {
        "src/app/__init__.py": {},
        "src/app/core.py": {"src/app/util.py": 1, "src/app/web/views.py": 2},
        "src/app/util.py": {"src/app/core.py": 1},
        "src/app/web/__init__.py": {"src/app/util.py": 1, "src/app/__init__.py": 2},
        "src/app/web/views.py": {"src/app/core.py": 1},
    }
