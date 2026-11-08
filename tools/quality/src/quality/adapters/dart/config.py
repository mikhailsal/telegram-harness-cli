"""The native configuration of a Dart component, checked before its tools run.

The formatter, the analyzer, and the test runner read their settings from files
the candidate controls, and the runner loads the component's own packages. The
profile requires the files that make those runs reproducible: a pubspec.yaml
with a package name, a lockfile that locks the pinned packages, and analysis
options that keep the strict floor (decision 0012). Flutter packages and web
builds are rejected rather than partly checked, because the profile runs the
Dart VM only.

The package name is written into the generated coverage test, so it must be a
valid Dart identifier of the form pub accepts.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import cast

from quality.adapters.dart.lockfile import PUBSPEC, VM_ONLY, Lock, check_lock
from quality.adapters.dart.options import check_options, check_test_config
from quality.adapters.settings import Table, load_yaml_mapping, table
from quality.boundedio import ReportError
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component

CONFIG = Rule(
    id="dart.config",
    title="Dart configuration is missing, unpinned, or not a Dart VM package",
    requirement="R04",
    explanation=(
        "A Dart component needs a pubspec.yaml with a package name, a pubspec.lock that locks "
        "the pinned test, coverage, and lints packages, and an analysis_options.yaml that "
        "includes the recommended lints, sets the strict language modes, and lowers or turns "
        "off no diagnostic. dart_test.yaml may only tune timeouts, concurrency, and stack "
        "traces. Flutter packages and web builds are not supported. The analyzer's findings in "
        "pubspec.yaml and analysis_options.yaml, such as an unknown or deprecated lint, are "
        "reported here too."
    ),
    remedy=(
        "Fix the named file; pin the packages with exact versions in dev_dependencies and run "
        "`dart pub get`."
    ),
)
RULES = (CONFIG,)

NAME = re.compile(r"[a-z_][a-z0-9_]*")
DEPENDENCIES = ("dependencies", "dev_dependencies", "dependency_overrides")
WEB_COMPILERS = "build_web_compilers"


def dart_files(files: tuple[str, ...]) -> tuple[str, ...]:
    """Return the Dart files of a list."""
    return tuple(path for path in files if PurePosixPath(path).suffix == ".dart")


@dataclass(frozen=True, slots=True)
class DartConfig:
    """The package name and the lockfile a component resolves from."""

    name: str
    lock: Lock


@dataclass(slots=True)
class Findings:
    """Problems found in one component's configuration files."""

    component: Component
    found: list[Diagnostic] = field(default_factory=list[Diagnostic])

    def add(self, file: str, message: str) -> None:
        """Record a problem in a file relative to the component directory."""
        location = Location(posixpath.normpath(posixpath.join(self.component.root, file)))
        self.found.append(
            CONFIG.diagnostic(message, location=location, component=self.component.id)
        )


def _dependency(name: str, spec: object, report: Findings) -> None:
    sdk = cast(Table, spec).get("sdk") if isinstance(spec, Mapping) else None
    if sdk is not None:
        report.add(PUBSPEC, f"{name} comes from the {sdk} SDK; {VM_ONLY}")
    elif name == WEB_COMPILERS:
        report.add(PUBSPEC, f"{name} compiles for the web; {VM_ONLY}")


def _variant(pubspec: Table, report: Findings) -> None:
    if "flutter" in pubspec:
        report.add(PUBSPEC, f"a `flutter` section makes this a Flutter package; {VM_ONLY}")
    if "flutter" in (table(pubspec, "environment") or {}):
        report.add(PUBSPEC, f"an `environment.flutter` constraint needs Flutter; {VM_ONLY}")
    for section in DEPENDENCIES:
        for name, spec in (table(pubspec, section) or {}).items():
            _dependency(str(name), spec, report)


def check_config(
    component: Component, snapshot: Path, pins: Mapping[str, str]
) -> tuple[DartConfig | None, Findings]:
    """Check a component's configuration; without a pubspec and lockfile nothing can run."""
    directory = snapshot / component.root
    report = Findings(component)
    try:
        pubspec = load_yaml_mapping(directory / PUBSPEC)
    except ReportError as error:
        report.add(PUBSPEC, f"a pubspec.yaml holding a YAML mapping is required ({error})")
        return None, report
    name = pubspec.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        report.add(PUBSPEC, "`name` must be lowercase letters, digits, and underscores")
    _variant(pubspec, report)
    check_options(directory, report.add)
    check_test_config(directory, report.add)
    lock = check_lock(directory, snapshot, pubspec, pins, report.add)
    if lock is None or not isinstance(name, str):
        return None, report
    return DartConfig(name, lock), report
