import json
from pathlib import Path

from quality.diagnostics import Severity
from quality.policy import Component
from quality.scenarios.suggest import suggestions
from quality.testkit.policies import ComponentSpec, build_policy


def component(*capabilities: str) -> Component:
    return build_policy(ComponentSpec(capabilities=capabilities)).components[0]


def hints(root: Path, files: list[str], *capabilities: str) -> list[str]:
    found = suggestions(component(*capabilities), root, files)
    assert {(d.rule, d.severity, d.component) for d in found} <= {
        ("scenarios.suggestion", Severity.INFO, "app")
    }
    return [d.message for d in found]


def test_a_component_without_hints_gets_no_suggestions(tmp_path: Path) -> None:
    assert hints(tmp_path, ["src/a.py"]) == []


def test_python_dependencies_and_scripts_are_hints(tmp_path: Path) -> None:
    requirements = ["Flask[async]>=3", "click", "requests ; python_version>'3'", 7]
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "a"\ndependencies = {json.dumps(requirements)}\n'
        '[project.scripts]\na = "a:main"\n'
    )
    assert hints(tmp_path, []) == [
        "'app' may have the 'http-api' capability: it depends on 'flask'",
        (
            "'app' may have the 'cli' capability: it depends on 'click', "
            "pyproject.toml declares scripts"
        ),
    ]


def test_declared_capabilities_are_not_suggested(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "a"\ndependencies = ["typer"]\n')
    assert hints(tmp_path, [], "cli") == []


def test_node_php_and_dart_manifests_are_read(tmp_path: Path) -> None:
    package = {"dependencies": {"express": "5", "pg": "8"}, "bin": {"a": "cli.js"}}
    (tmp_path / "package.json").write_text(json.dumps(package))
    composer = {"require": {"php": ">=8.4", "doctrine/migrations": "3"}}
    (tmp_path / "composer.json").write_text(json.dumps(composer))
    (tmp_path / "pubspec.yaml").write_text(
        "name: a\ndependencies:\n  shelf: ^1.4\nexecutables:\n  a: main\n"
    )
    assert hints(tmp_path, []) == [
        "'app' may have the 'http-api' capability: it depends on 'express', it depends on 'shelf'",
        "'app' may have the 'database' capability: it depends on 'pg'",
        "'app' may have the 'cli' capability: its manifest declares executables",
        "'app' may have the 'migrations' capability: it depends on 'doctrine/migrations'",
    ]


def test_an_openapi_document_and_a_migrations_directory_are_hints(tmp_path: Path) -> None:
    files = ["api/openapi.yaml", "db/migrations/001.sql", "db/migrations/002.sql", "migrations"]
    assert hints(tmp_path, files) == [
        "'app' may have the 'http-api' capability: it has the OpenAPI document 'api/openapi.yaml'",
        "'app' may have the 'migrations' capability: it has migrations under 'db/migrations'",
    ]


def test_unreadable_manifests_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{")
    (tmp_path / "pyproject.toml").write_text("[project]\ndependencies = 'flask'\n")
    assert hints(tmp_path, []) == []
