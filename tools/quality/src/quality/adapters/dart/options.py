"""The analysis options and test runner settings a Dart component may use.

`dart analyze` reads `analysis_options.yaml` from the analyzed file's
directory upwards, so every options file in the component decides how some of
its files are checked, and each one must keep the profile's floor: the
recommended lints, the three strict language modes, no diagnostic lowered to
`ignore` or `info`, and no lint rule turned off. The gate names the files to
analyze, which the analyzer checks even when `analyzer.exclude` matches them,
so an exclude cannot hide a file and is not restricted.

`dart_test.yaml` may only tune timeouts, concurrency, and stack traces. Every
other key changes which tests run, on which platform, or how they are reported,
so the gate's own command line would no longer decide the verdict.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from quality.adapters.dart.lockfile import VM_ONLY
from quality.adapters.settings import Report, Table, label, load_yaml_mapping, strings, table
from quality.boundedio import ReportError

OPTIONS = "analysis_options.yaml"
TEST_CONFIG = "dart_test.yaml"
RECOMMENDED = "package:lints/recommended.yaml"
STRICT = ("strict-casts", "strict-inference", "strict-raw-types")
LOWERED = frozenset({"ignore", "info"})
ACCEPTED = ("timeout", "concurrency", "fold_stack_traces", "chain_stack_traces", "verbose_trace")
# Directories that tools write or that belong to other tools; they hold no options of the component.
PRUNED = frozenset({".dart_tool", ".git", "node_modules"})


def option_files(directory: Path) -> list[Path]:
    """Return every analysis options file in the component directory, in a stable order."""
    found: list[Path] = []
    for current, directories, files in os.walk(directory):
        directories[:] = sorted(name for name in directories if name not in PRUNED)
        if OPTIONS in files:
            found.append(Path(current) / OPTIONS)
    return found


def _included(options: Table) -> list[str]:
    value = options.get("include")
    return [value] if isinstance(value, str) else strings(value)


def _settings(options: Table, *keys: str) -> Mapping[str, object]:
    return table(options, *keys) or {}


def _options(path: Path, name: str, add: Report) -> None:
    try:
        options = load_yaml_mapping(path)
    except ReportError as error:
        add(name, f"the analysis options are unusable: {error}")
        return
    if RECOMMENDED not in _included(options):
        add(name, f"`include` must name {RECOMMENDED}")
    language = _settings(options, "analyzer", "language")
    for mode in STRICT:
        if language.get(mode) is not True:
            add(name, f"`analyzer.language.{mode}` must be true")
    for code, severity in _settings(options, "analyzer", "errors").items():
        if severity in LOWERED:
            add(name, f"`analyzer.errors` lowers {code} to {severity}; every diagnostic fails")
    for rule, enabled in _settings(options, "linter", "rules").items():
        if enabled is False:
            add(name, f"`linter.rules` turns {rule} off")


def check_options(directory: Path, add: Report) -> None:
    """Check the analysis options at the component root and below it."""
    if not (directory / OPTIONS).is_file():
        add(OPTIONS, f"an {OPTIONS} at the component root is required")
    for path in option_files(directory):
        _options(path, label(path, directory), add)


def check_test_config(directory: Path, add: Report) -> None:
    """Check that `dart_test.yaml`, when present, only tunes how the tests run."""
    path = directory / TEST_CONFIG
    if not path.is_file():
        return
    try:
        settings = load_yaml_mapping(path)
    except ReportError as error:
        add(TEST_CONFIG, f"the test configuration is unusable: {error}")
        return
    for key, value in settings.items():
        if key == "platforms" and strings(value) != ["vm"] and value != "vm":
            add(TEST_CONFIG, f"`platforms` names a platform other than vm; {VM_ONLY}")
        elif key not in ACCEPTED:
            accepted = ", ".join(ACCEPTED)
            add(
                TEST_CONFIG, f"`{key}` changes which tests run or how; only {accepted} are accepted"
            )
