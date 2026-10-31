"""The enumerations of the policy: categories, roles, profiles, targets, capabilities, suites."""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    """File categories assigned to every candidate file."""

    SOURCE = "source"
    TESTS = "tests"
    DOCS = "docs"
    CONFIG = "config"
    GENERATED = "generated"
    VENDOR = "vendor"
    ASSETS = "assets"


class Role(StrEnum):
    """Component roles; see decision 0003."""

    APPLICATION = "application"
    TOOLING = "tooling"
    HELPER = "helper"
    EXAMPLE = "example"


class Profile(StrEnum):
    """Tier 1 language profiles."""

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    DART = "dart"
    PHP = "php"


class ExecutionTarget(StrEnum):
    """Where a TypeScript component's code runs (decision 0017)."""

    NODE = "node"
    BROWSER = "browser"


class Framework(StrEnum):
    """The user-interface frameworks a browser component may use (decision 0017)."""

    REACT = "react"


class Bundler(StrEnum):
    """The bundlers that build a browser component (decision 0017)."""

    VITE = "vite"


class Capability(StrEnum):
    """Behavioral capabilities a component can declare (decision 0014)."""

    LIBRARY = "library"
    CLI = "cli"
    HTTP_API = "http-api"
    DATABASE = "database"
    MIGRATIONS = "migrations"
    CONTRACTS = "contracts"
    QUEUES = "queues"
    UI = "ui"
    AUTHORIZATION = "authorization"


class Enhancement(StrEnum):
    """Optional enhanced test profiles a component can enable (decision 0014)."""

    PROPERTY = "property"
    PERFORMANCE = "performance"
    MUTATION = "mutation"


class Suite(StrEnum):
    """A declared test suite; only unit suites feed the coverage thresholds."""

    UNIT = "unit"
    INTEGRATION = "integration"
    CONTRACT = "contract"
    E2E = "e2e"
    ACCEPTANCE = "acceptance"
