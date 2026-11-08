"""Hints at capabilities a component may have but does not declare (R08.5).

Hints come from the dependencies a component's package manifest names, its
declared executables, an OpenAPI document, and a migrations directory. They
are never requirements: a dependency can be unused, and a component's real
boundaries are known only to its owners.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import MappingProxyType

from quality.adapters.settings import (
    Table,
    load_json_object,
    load_toml,
    load_yaml_mapping,
    strings,
    table,
)
from quality.boundedio import ReportError
from quality.diagnostics import Diagnostic
from quality.policy import Capability, Component
from quality.scenarios.rules import SUGGESTION

HTTP, DB, MIGRATIONS = Capability.HTTP_API, Capability.DATABASE, Capability.MIGRATIONS
QUEUES, CLI, UI, CONTRACTS = Capability.QUEUES, Capability.CLI, Capability.UI, Capability.CONTRACTS
HINTS = MappingProxyType(
    {
        **dict.fromkeys(("fastapi", "flask", "django", "starlette", "aiohttp", "litestar"), HTTP),
        **dict.fromkeys(("express", "fastify", "koa", "hono", "@nestjs/core"), HTTP),
        **dict.fromkeys(("laravel/framework", "symfony/http-kernel", "slim/slim"), HTTP),
        **dict.fromkeys(("shelf", "dart_frog"), HTTP),
        **dict.fromkeys(("sqlalchemy", "psycopg", "psycopg2", "asyncpg", "pymysql", "peewee"), DB),
        **dict.fromkeys(("pg", "mysql2", "sqlite3", "better-sqlite3", "@prisma/client"), DB),
        **dict.fromkeys(("typeorm", "sequelize", "mongoose", "drizzle-orm", "knex"), DB),
        **dict.fromkeys(("doctrine/orm", "doctrine/dbal", "illuminate/database"), DB),
        **dict.fromkeys(("postgres", "drift", "sqflite", "mysql_client"), DB),
        **dict.fromkeys(("alembic", "yoyo-migrations", "doctrine/migrations", "umzug"), MIGRATIONS),
        **dict.fromkeys(("celery", "kombu", "pika", "aiokafka", "dramatiq", "rq"), QUEUES),
        **dict.fromkeys(("amqplib", "kafkajs", "bullmq", "php-amqplib/php-amqplib"), QUEUES),
        **dict.fromkeys(("click", "typer", "commander", "yargs", "symfony/console", "args"), CLI),
        **dict.fromkeys(("react", "vue", "svelte", "@angular/core", "solid-js", "flutter"), UI),
        **dict.fromkeys(
            ("pact-python", "@pact-foundation/pact", "pact-foundation/pact-php"), CONTRACTS
        ),
    }
)
OPENAPI = ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json")
SEPARATORS = "<>=!~;[ ("


def _requirement_name(requirement: str) -> str:
    """Return the package name of a PEP 508 requirement such as `flask[async]>=3`."""
    cut = min((requirement.find(ch) for ch in SEPARATORS if ch in requirement), default=None)
    return requirement[:cut].strip().lower()


def _python(root: Path) -> Iterator[tuple[str, Capability]]:
    project = table(load_toml(root / "pyproject.toml"), "project") or {}
    for requirement in strings(project.get("dependencies")):
        name = _requirement_name(requirement)
        if name in HINTS:
            yield f"it depends on '{name}'", HINTS[name]
    if table(project, "scripts"):
        yield "pyproject.toml declares scripts", CLI


def _keys(document: Table, *sections: str) -> Iterator[str]:
    for section in sections:
        yield from table(document, section) or {}


def _named(document: Table, *sections: str) -> Iterator[tuple[str, Capability]]:
    for name in _keys(document, *sections):
        if name in HINTS:
            yield f"it depends on '{name}'", HINTS[name]
    if document.get("bin") or document.get("executables"):
        yield "its manifest declares executables", CLI


def _node(root: Path) -> Iterator[tuple[str, Capability]]:
    return _named(load_json_object(root / "package.json"), "dependencies")


def _php(root: Path) -> Iterator[tuple[str, Capability]]:
    return _named(load_json_object(root / "composer.json"), "require")


def _dart(root: Path) -> Iterator[tuple[str, Capability]]:
    return _named(load_yaml_mapping(root / "pubspec.yaml"), "dependencies")


READERS: tuple[tuple[str, Callable[[Path], Iterator[tuple[str, Capability]]]], ...] = (
    ("pyproject.toml", _python),
    ("package.json", _node),
    ("composer.json", _php),
    ("pubspec.yaml", _dart),
)


def _files(root: Path, files: Iterable[str]) -> Iterator[tuple[str, Capability]]:
    for path in files:
        parts = Path(path).parts
        if parts[-1] in OPENAPI:
            yield f"it has the OpenAPI document '{path}'", HTTP
        if "migrations" in parts[:-1]:
            directory = "/".join(parts[: parts.index("migrations") + 1])
            yield f"it has migrations under '{directory}'", MIGRATIONS
    for manifest, reader in READERS:
        if (root / manifest).is_file():
            try:
                yield from list(reader(root))
            except ReportError:
                continue


def suggestions(component: Component, root: Path, files: Iterable[str]) -> list[Diagnostic]:
    """Hint at undeclared capabilities; `files` are the component's paths relative to `root`."""
    reasons: dict[Capability, list[str]] = {}
    for reason, capability in _files(root, files):
        if capability not in component.capabilities:
            reasons.setdefault(capability, []).append(reason)
    return [
        SUGGESTION.diagnostic(
            f"'{component.id}' may have the '{capability}' capability: "
            + ", ".join(dict.fromkeys(found)),
            component=component.id,
        )
        for capability, found in reasons.items()
    ]
