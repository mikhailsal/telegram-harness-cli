from pathlib import Path

import pytest

from quality.adapters.typescript.config import (
    CONFIG,
    check_config,
    javascript,
    typescript_files,
)
from quality.diagnostics import Diagnostic
from quality.testkit.browserfakes import ALL_PINS
from quality.testkit.nodefakes import (
    PINS,
    browser_component,
    typescript_component,
    write_component,
)

APP = typescript_component()


def located(found: list[Diagnostic]) -> list[tuple[str | None, str]]:
    assert {d.rule for d in found} <= {CONFIG.id}
    return [(d.location.path if d.location else None, d.message) for d in found]


def test_typescript_files_include_declarations() -> None:
    files = ("a.ts", "b.tsx", "c.mts", "d.cts", "e.d.ts", "f.js", "g.json")
    assert typescript_files(files) == ("a.ts", "b.tsx", "c.mts", "d.cts", "e.d.ts")


def test_a_complete_configuration_passes(snapshot: Path) -> None:
    write_component(snapshot / "app")
    settings, report = check_config(APP, snapshot, PINS)
    assert report.found == []
    assert settings is not None
    assert settings.lock.directory == snapshot / "app"


def test_a_browser_component_also_locks_the_browser_packages(snapshot: Path) -> None:
    locked = {name: pin for name, pin in ALL_PINS.items() if name not in {"globals", "jsdom"}}
    write_component(snapshot / "web", {**locked, "react": "18.3.1"})
    _, report = check_config(browser_component(), snapshot, ALL_PINS)
    assert located(report.found) == [
        (
            "web/package-lock.json",
            "node_modules/react is locked at '18.3.1'; the profile pins react 19.3.1",
        ),
        ("web/package-lock.json", "the lockfile does not lock jsdom; add jsdom@30.1.3"),
    ]
    assert check_config(typescript_component("web"), snapshot, ALL_PINS)[1].found == []


def test_missing_files_are_reported_at_the_component(snapshot: Path) -> None:
    (snapshot / "app").mkdir()
    (snapshot / "app" / "package.json").write_text("[]")
    (snapshot / "app" / ".eslintrc.json").write_text("{}")
    settings, report = check_config(APP, snapshot, PINS)
    assert settings is None
    found = located(report.found)
    assert found[0][0] == "app/package.json"
    assert found[0][1].startswith("a package.json holding a JSON object is required (")
    assert found[1:] == [
        ("app/tsconfig.json", "a tsconfig.json at the component root is required"),
        ("app/eslint.config.js", "an ESLint flat configuration (eslint.config.*) is required"),
        (
            "app/.eslintrc.json",
            "ESLint 10 ignores legacy configuration; move it to eslint.config.*",
        ),
        (
            "app/package-lock.json",
            (
                "no package-lock.json in the component or in a workspace root that lists it; "
                "run `npm install`"
            ),
        ),
    ]


@pytest.mark.parametrize("name", ["eslint.config.mjs", "eslint.config.cts"])
def test_any_flat_configuration_name_is_accepted(snapshot: Path, name: str) -> None:
    directory = write_component(snapshot / "app")
    (directory / "eslint.config.js").rename(directory / name)
    assert check_config(APP, snapshot, PINS)[1].found == []


def test_a_root_component_names_files_without_a_prefix(snapshot: Path) -> None:
    write_component(snapshot)
    (snapshot / "tsconfig.json").unlink()
    _, report = check_config(typescript_component("."), snapshot, PINS)
    assert located(report.found) == [
        ("tsconfig.json", "a tsconfig.json at the component root is required")
    ]


def test_javascript_sources_are_reported() -> None:
    files = ("app/src/a.ts", "app/src/b.js", "app/tests/c.test.mjs", "app/src/d.cjs")
    found = javascript(APP, files)
    assert [d.location.path for d in found if d.location] == [
        "app/src/b.js",
        "app/tests/c.test.mjs",
        "app/src/d.cjs",
    ]
    assert found[0].message == (
        "JavaScript is not type-checked or measured by the TypeScript profile; "
        "convert the file to TypeScript"
    )
    assert {d.component for d in found} == {"app"}
