"""`quality fix` for Dart: `dart fix --apply` and `dart format`, applied through a copy.

Resolving the component's packages writes `.dart_tool/` into the lock
directory, so the fixer works on a temporary copy of the working tree, resolves
the lockfile offline there, runs `dart fix --apply` on the component directory
and `dart format` on the named files, and copies back each changed file whose
working-tree content is still the copied one. Nothing is staged, and what
remains is reported as the check reports it (decision 0012).
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.copies import copy_back, copy_tree
from quality.adapters.dart.analyzer import dart_analyze
from quality.adapters.dart.config import check_config, dart_files
from quality.adapters.dart.environment import Environments
from quality.adapters.dart.formatter import UNPARSABLE_EXIT, dart_format
from quality.adapters.native import run_tool
from quality.adapters.target import Target
from quality.diagnostics import Diagnostic
from quality.policy import Component

TIMEOUT = 900.0
DART_TOOL = ".dart_tool"


@dataclass(slots=True)
class DartFixer:
    """Applies the analyzer's fixes and the pinned formatter."""

    environments: Environments
    pins: Mapping[str, str]
    listing: Callable[[Path], Iterable[str]]

    def _fixed(self, component: Component, copy: Path, files: tuple[str, ...]) -> list[Diagnostic]:
        settings, report = check_config(component, copy, self.pins)
        if settings is None or report.found or not files:
            return report.found
        work = copy.parent / "work"
        sdk = self.environments.install(settings.lock.directory, work, label=component.id)
        target = Target(component, copy, files)
        home = work / "dart-home" / component.id
        fixes = sdk.command("fix", "--apply", str(target.directory))
        run_tool(
            sdk.run_in(fixes, target.directory, home, timeout=TIMEOUT),
            name="dart fix",
            accepted={0},
        )
        formatting = sdk.command("format", *target.arguments())
        run_tool(
            sdk.run_in(formatting, target.directory, home, timeout=TIMEOUT),
            name="dart format",
            accepted={0, UNPARSABLE_EXIT},
        )
        return dart_format(target, sdk=sdk, home=home) + dart_analyze(target, sdk=sdk, home=home)

    def fix(self, component: Component, root: Path, files: tuple[str, ...]) -> list[Diagnostic]:
        """Fix and format the files in a copy, copy them back, and return what remains."""
        checked = dart_files(files)
        if not checked:
            return check_config(component, root, self.pins)[1].found
        with tempfile.TemporaryDirectory(prefix=f"quality-fix-{component.id}-") as scratch:
            copy = Path(scratch) / "tree"
            copy_tree(root, copy, sorted(set(self.listing(root))), skipped=DART_TOOL)
            before = {
                path: (copy / path).read_bytes() for path in checked if (copy / path).is_file()
            }
            found = self._fixed(component, copy, tuple(before))
            copy_back(copy, root, before)
        return found
