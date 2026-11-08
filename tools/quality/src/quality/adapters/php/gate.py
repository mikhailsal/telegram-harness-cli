"""The gate's own settings for PHP CS Fixer and PHPStan, written into the work area.

The component's `.php-cs-fixer.php` and `phpstan.neon` files are PHP and NEON
that the candidate controls; the formatter would execute the first. The gate
passes its own files instead (decision 0013): PER Coding Style without risky
rules for the formatter, and PHPStan's highest level with its temporary
directory in the work area. Each work area writes them once.
"""

from __future__ import annotations

from pathlib import Path

CS_FIXER = """\
<?php

// Written by the quality gate: PER Coding Style, no risky rules, no cache.
return (new PhpCsFixer\\Config())
    ->setRules(['@PER-CS' => true])
    ->setRiskyAllowed(false)
    ->setUsingCache(false);
"""
PHPSTAN = """\
# Written by the quality gate: the highest level, without a baseline.
parameters:
    level: max
    tmpDir: {tmp}
"""


def settings(work: Path) -> tuple[Path, Path]:
    """Write the formatter's and the analyzer's settings once; return their paths."""
    directory = work / "php-gate"
    cs_fixer, phpstan = directory / "cs-fixer.php", directory / "phpstan.neon"
    if not phpstan.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        cs_fixer.write_text(CS_FIXER, encoding="utf-8")
        tmp = directory / "phpstan-tmp"
        phpstan.write_text(PHPSTAN.format(tmp=tmp.as_posix()), encoding="utf-8")
    return cs_fixer, phpstan
