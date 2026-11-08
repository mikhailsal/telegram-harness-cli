from pathlib import Path

import pytest

from quality.adapters.typescript.browser import (
    ACCESSIBILITY,
    HOOKS,
    TEST_RULES,
    browser_files,
    browser_sources,
    server_module,
)
from quality.testing import StaticRequest
from quality.testkit.nodefakes import browser_component

SERVER = '[components.browser]\nserver_modules = ["src/server/**", "src/*.server.ts"]'
WEB = browser_component(extra=SERVER)


@pytest.mark.parametrize(
    ("path", "server"),
    [
        ("web/src/server/db.ts", True),
        ("web/src/server/deep/pool.ts", True),
        ("web/src/auth.server.ts", True),
        ("web/src/nested/auth.server.ts", False),
        ("web/src/App.tsx", False),
    ],
)
def test_server_modules_match_relative_to_the_root(path: str, *, server: bool) -> None:
    assert server_module(WEB, path) is server


def test_the_files_are_sorted_for_the_browser_gates(tmp_path: Path) -> None:
    files = ("web/src/App.tsx", "web/src/use.ts", "web/src/server/db.ts", "web/tests/a.test.tsx")
    resources = ("web/src/app.css", "web/src/old.SCSS", "web/index.html", "web/public/a.svg")
    request = StaticRequest(
        WEB, tmp_path, tmp_path / "work", files, ("web/vite.config.ts",), resources
    )
    found = browser_files(request)
    assert found.sources == ("web/src/App.tsx", "web/src/use.ts")
    assert found.tests == ("web/tests/a.test.tsx",)
    assert found.styles == ("web/src/app.css",)
    assert found.variants == ("web/src/old.SCSS",)
    assert found.entries == ("web/index.html",)
    assert found.owned == frozenset({*files, "web/vite.config.ts", *resources})
    assert found.required_rules() == {
        "web/src/App.tsx": HOOKS + ACCESSIBILITY,
        "web/src/use.ts": HOOKS,
        "web/tests/a.test.tsx": TEST_RULES,
    }


def test_browser_sources_leave_out_tests_server_modules_and_other_files() -> None:
    files = ("web/src/App.tsx", "web/src/server/db.ts", "web/tests/a.test.tsx", "web/src/a.css")
    assert browser_sources(WEB, files) == ("web/src/App.tsx",)
