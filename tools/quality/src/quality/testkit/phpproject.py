"""A scratch PHP component that the shared Composer cache installs offline.

The component requires exactly what the core's PHP helper requires, so the
helper's composer.lock is its lockfile: Composer's content hash covers the
requirements but not the name, the description, or the autoload sections.
Bootstrapping this repository installed the helper's packages, development
ones included, which filled the cache, so nothing is downloaded.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from quality.adapters.php.helper import HELPER
from quality.boundedio import JsonValue

REQUIREMENTS = ("require", "require-dev")
CORE = """\
<?php

declare(strict_types=1);

namespace App;

final class Sign
{
    public static function of(int $value): int
    {
        if ($value < 0) {
            return -1;
        }
        return 1;
    }
}
"""
TESTS = """\
<?php

declare(strict_types=1);

namespace App\\Tests;

use App\\Sign;
use PHPUnit\\Framework\\TestCase;

final class SignTest extends TestCase
{
    public function testSign(): void
    {
        self::assertSame(-1, Sign::of(-3));
        self::assertSame(1, Sign::of(4));
    }
}
"""
# A documented type that contradicts the native one: PHPStan rejects it, PHP does not.
TYPED = CORE.replace("    public static", "    /** @param string $value */\n    public static")


def requirements() -> dict[str, JsonValue]:
    """Return the helper's `require` and `require-dev` sections."""
    document = json.loads((HELPER / "composer.json").read_text(encoding="utf-8"))
    return {section: document[section] for section in REQUIREMENTS}


def manifest(**fields: JsonValue) -> str:
    """Return a composer.json with the helper's requirements and the `App` prefix in `src/`."""
    document: dict[str, JsonValue] = {
        "type": "project",
        **requirements(),
        "autoload": {"psr-4": {"App\\": "src/"}},
        "autoload-dev": {"psr-4": {"App\\Tests\\": "tests/"}},
    }
    document.update(fields)
    return json.dumps(document, indent=4) + "\n"


def lockfile() -> str:
    """Return the helper's lockfile, which locks what `manifest` requires."""
    return (HELPER / "composer.lock").read_text(encoding="utf-8")


def write(directory: Path, files: Mapping[str, str]) -> None:
    """Write text files below `directory`."""
    for path, text in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def php_project(directory: Path, files: Mapping[str, str] | None = None) -> Path:
    """Write a complete component with `files`, or the default ones, into `directory`."""
    sources = {"src/Sign.php": CORE, "tests/SignTest.php": TESTS}
    write(
        directory,
        {
            "composer.json": manifest(),
            "composer.lock": lockfile(),
            **(sources if files is None else files),
        },
    )
    return directory
