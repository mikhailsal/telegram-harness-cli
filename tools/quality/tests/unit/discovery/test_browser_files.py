import pytest

from quality.discovery import classify, detect
from quality.discovery.classify import Classified
from quality.policy import Category, Policy
from quality.testkit.policies import (
    BROWSER_STACK,
    ComponentSpec,
    browser_spec,
    build_policy,
    classification_toml,
)

WEB = browser_spec(root="apps/web")
NODE = ComponentSpec(id="api", root="apps/api", profile="typescript")


def run(policy: Policy, path: str) -> Classified:
    return classify(policy, path, detect(path))


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("apps/web/dist/assets/index-3f2a.js", Category.GENERATED),
        ("apps/web/dist/index.html", Category.GENERATED),
        ("apps/web/public/favicon.svg", Category.ASSETS),
        ("apps/web/public/sw.js", Category.ASSETS),
        ("apps/web/public/manifest.json", Category.ASSETS),
        ("apps/web/index.html", Category.ASSETS),
        ("apps/web/src/App.module.css", Category.ASSETS),
        ("apps/web/src/logo.svg", Category.ASSETS),
        ("apps/web/src/main.tsx", Category.SOURCE),
        ("apps/web/src/main.tsx.map", Category.GENERATED),
        ("apps/web/tests/fixtures/page.html", Category.TESTS),
    ],
)
def test_browser_files_are_classified_by_their_place_and_type(
    path: str, category: Category
) -> None:
    found = run(build_policy(WEB, NODE), path)
    assert (found.record.category, found.diagnostics) == (category, [])


def test_the_browser_directories_mean_nothing_in_a_node_component() -> None:
    policy = build_policy(WEB, NODE)
    found = run(policy, "apps/api/dist/server.js")
    assert found.record.category is Category.SOURCE
    assert [d.rule for d in found.diagnostics] == ["discovery.unowned-source"]
    assert run(policy, "apps/api/public/manifest.json").record.category is Category.CONFIG


def test_source_roots_and_explicit_classification_come_first() -> None:
    rooted = ComponentSpec(
        id="web",
        root="apps/web",
        profile="typescript",
        source=("src", "dist"),
        capabilities=("ui",),
        extra=BROWSER_STACK,
    )
    assert run(build_policy(rooted), "apps/web/dist/a.ts").record.category is Category.SOURCE
    vendor = classification_toml("apps/web/public/vendor/**", "vendor")
    found = run(build_policy(WEB, extra=vendor), "apps/web/public/vendor/lib.js")
    assert (found.record.category, found.record.explicit) == (Category.VENDOR, True)


@pytest.mark.parametrize("name", ["bundle.js.map", "style.css.map"])
def test_source_maps_are_generated_everywhere(name: str) -> None:
    found = run(build_policy(), f"build/{name}")
    assert (found.record.category, found.diagnostics) == (Category.GENERATED, [])


def test_stylus_is_an_asset() -> None:
    assert run(build_policy(WEB), "apps/web/src/a.styl").record.category is Category.ASSETS
