from dataclasses import replace

from quality.ci import changed_control, control_files
from quality.ci.control import control_patterns, is_control
from quality.discovery import FileRecord, Region, classify, detect
from quality.policy import Category, CiPolicy
from quality.testkit.policies import ComponentSpec, browser_spec, build_policy

POLICY = build_policy(
    ComponentSpec(id="app", root="app", scenarios="scenarios.toml"),
    ComponentSpec(id="core", root="tools/core", role="tooling"),
    ComponentSpec(id="root", root=".", source=("lib",), unit=("spec",), role="helper"),
)
CI = CiPolicy(
    workflow=".github/workflows/quality.yml",
    required_jobs=["gate"],
    codeowners="docs/CODEOWNERS",
    control=["ci/**", "quality.toml"],
)


def record(path: str, category: Category = Category.SOURCE) -> FileRecord:
    return FileRecord(path, category, None, None, Region.OTHER)


def test_control_patterns_cover_the_fixed_files_policy_roots_and_extras_once() -> None:
    assert control_patterns(POLICY, CI) == [
        "quality.toml",
        "quality",
        ".gitattributes",
        ".github/**",
        ".gitlab-ci.yml",
        "docs/CODEOWNERS",
        ".github/workflows/quality.yml",
        "ci/**",
        "app/scenarios.toml",
        "tools/core/**",
        "**",
    ]


def test_a_shipped_example_is_owned_like_the_tooling() -> None:
    example = ComponentSpec(id="example", root="examples/web", profile="typescript", role="example")
    policy = build_policy(ComponentSpec(id="app", root="app"), example)
    assert control_patterns(policy, CI)[-1] == "examples/web/**"


def test_patterns_match_whole_paths() -> None:
    patterns = ["tools/core/**", "quality"]
    assert is_control("tools/core/src/a.py", patterns)
    assert is_control("quality", patterns)
    assert not is_control("tools/corex/a.py", patterns)
    assert not is_control("app/quality", patterns)


def test_native_configuration_is_a_control_file_wherever_it_lives() -> None:
    policy = build_policy(ComponentSpec(id="app", root="app"))
    records = [
        record("app/src/main.py"),
        record("app/pyproject.toml", Category.CONFIG),
        record(".github/workflows/quality.yml", Category.CONFIG),
        record("ci/gitlab.yml", Category.DOCS),
    ]
    assert control_files(policy, CI, records) == [
        "app/pyproject.toml",
        ".github/workflows/quality.yml",
        "ci/gitlab.yml",
    ]


def test_changed_control_files_are_listed_with_deleted_ones_by_pattern() -> None:
    policy = build_policy(ComponentSpec(id="app", root="app"))
    records = [record("app/src/main.py"), record("app/pyproject.toml", Category.CONFIG)]
    changed = ["app/src/main.py", "app/pyproject.toml", ".github/CODEOWNERS", "app/pyproject.toml"]
    found = list(changed_control(policy, CI, records, changed))
    assert [(d.rule, d.severity, d.message) for d in found] == [
        (
            "ci.control-changed",
            "info",
            "control file '.github/CODEOWNERS' changed; it needs code-owner review",
        ),
        (
            "ci.control-changed",
            "info",
            "control file 'app/pyproject.toml' changed; it needs code-owner review",
        ),
    ]
    assert found[0].location is not None
    assert found[0].location.path == ".github/CODEOWNERS"


def test_the_settings_of_a_browser_component_are_control_files() -> None:
    web = replace(browser_spec(), e2e=("e2e",), scenarios="scenarios.toml")
    policy = build_policy(ComponentSpec(id="app", root="app"), web)
    settings = [
        "web/playwright.config.ts",
        "web/vite.config.ts",
        "web/vitest.config.ts",
        "web/eslint.config.js",
        "web/stylelint.config.mjs",
        "web/tsconfig.json",
        "web/package.json",
        "web/package-lock.json",
        "web/scenarios.toml",
    ]
    code = ["web/src/main.tsx", "web/e2e/counter.spec.ts", "web/index.html"]
    records = [classify(policy, path, detect(path)).record for path in (*settings, *code)]
    assert control_files(policy, CI, records) == settings
