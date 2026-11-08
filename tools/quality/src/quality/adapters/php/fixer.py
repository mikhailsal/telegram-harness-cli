"""`quality fix` for PHP: PHP CS Fixer applied through a copy.

Installing the component's packages writes `vendor/` into the component
directory, so the fixer works on a temporary copy of the working tree without
`vendor/`, validates and installs the lockfile offline there, runs the locked
PHP CS Fixer's `fix` on the named files under the gate's settings, and copies
back each changed file whose working-tree content is still the copied one.
Nothing is staged, and what remains is reported as the check reports it
(decision 0013). PHPStan has no fixes to apply.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.copies import copy_back, copy_tree
from quality.adapters.native import run_tool
from quality.adapters.php import gate
from quality.adapters.php.analyzer import phpstan
from quality.adapters.php.composer import Environments
from quality.adapters.php.config import VENDOR, check_config, php_files
from quality.adapters.php.formatter import ACCEPTED, TIMEOUT, TOOL, cs_check, cs_fixer
from quality.adapters.target import Target
from quality.diagnostics import Diagnostic
from quality.policy import Component


@dataclass(slots=True)
class PhpFixer:
    """Applies the pinned formatter's fixes."""

    environments: Environments
    pins: Mapping[str, str]
    listing: Callable[[Path], Iterable[str]]

    def _fixed(self, component: Component, copy: Path, files: tuple[str, ...]) -> list[Diagnostic]:
        settings, report = check_config(component, copy, self.pins)
        if settings is None or report.found or not files:
            return report.found
        work = copy.parent / "work"
        directory = settings.lock.directory
        php = self.environments.install(directory, work, label=component.id, add=report.add)
        if report.found:
            return report.found
        target = Target(component, copy, files)
        home = work / "php-home" / component.id
        formatting, analysis = gate.settings(work)
        fixes = cs_fixer(php, target, formatting, "fix")
        run_tool(
            php.run_in(fixes, target.directory, home, timeout=TIMEOUT), name=TOOL, accepted=ACCEPTED
        )
        remaining = cs_check(target, php=php, settings=formatting, home=home)
        return remaining + phpstan(target, php=php, settings=analysis, home=home)

    def fix(self, component: Component, root: Path, files: tuple[str, ...]) -> list[Diagnostic]:
        """Fix the files in a copy, copy them back, and return what remains."""
        checked = php_files(files)
        if not checked:
            return check_config(component, root, self.pins)[1].found
        with tempfile.TemporaryDirectory(prefix=f"quality-fix-{component.id}-") as scratch:
            copy = Path(scratch) / "tree"
            copy_tree(root, copy, sorted(set(self.listing(root))), skipped=VENDOR)
            before = {
                path: (copy / path).read_bytes() for path in checked if (copy / path).is_file()
            }
            found = self._fixed(component, copy, tuple(before))
            copy_back(copy, root, before)
        return found
