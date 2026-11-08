import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.adapters.typescript.browser import TEST_RULES
from quality.adapters.typescript.environment import Environments
from quality.adapters.typescript.fixer import (
    TypeScriptFixer,
    browser_pages,
    config_scripts,
    copy_back,
    copy_tree,
)
from quality.policy import Component
from quality.testkit.browserfakes import ALL_PINS, write_browser_component
from quality.testkit.nodefakes import (
    PINS,
    FakeNode,
    browser_component,
    install_tools,
    script,
    typescript_component,
    write_component,
)

APP = typescript_component()
# ESLint appends a marker to the files it fixes and reports files that mention `error`;
# Prettier rewrites `messy` to `tidy` and lists files that are still messy.
TOOLS = {
    "eslint": """\
for arg; do case "$arg" in --fix) fix=1 ;; esac; done
if [ -n "$fix" ]; then
  for arg; do case "$arg" in /*) echo "// linted" >> "$arg" ;; esac; done
  exit 1
fi
for arg; do
  case "$arg" in /*.ts) grep -q error "$arg" && hit="$arg" ;; esac
done
if [ -n "$hit" ]; then
  printf '[{"filePath": "%s", "messages": [{"ruleId": "no-x", "message": "x"}]}]' "$hit"
  exit 1
fi
echo '[]'
""",
    "prettier": """\
for arg; do case "$arg" in --write) write=1 ;; esac; done
for arg; do
  case "$arg" in
    /*.ts|/*.js|/*.css|/*.html)
      if [ -n "$write" ]; then sed -i 's/messy/tidy/' "$arg"; elif grep -q messy "$arg"; then
        echo "$arg"; listed=1
      fi ;;
  esac
done
[ -n "$listed" ] && exit 1
exit 0
""",
}


@dataclass(frozen=True, slots=True)
class Fixture:
    fixer: TypeScriptFixer
    root: Path
    fake: FakeNode


def build(fake_node: FakeNode, tmp_path: Path, component: Component = APP) -> Fixture:
    root = tmp_path / "worktree"
    browser = component.in_browser
    pins = ALL_PINS if browser else PINS
    directory = (write_browser_component if browser else write_component)(root / "app")
    (directory / "src" / "a.ts").write_text("export const a = 'messy';\n")
    (directory / "tests" / "a.test.ts").write_text("export const t = 1;\n")
    (directory / "eslint.config.js").write_text("export default [/* messy */];\n")
    (directory / "node_modules" / "left").mkdir(parents=True)
    template = tmp_path / "packages"
    install_tools(template, TOOLS, pins)
    fake_node.npm(f"cp -R '{template / 'node_modules'}' \"$PWD\"/\n")
    answer = tmp_path / "eslint-config.json"
    body = f"""printf '{{"protocol": 1, "files": [' > '{answer}'
sed -n 's/.*"files": \\[\\([^]]*\\)\\].*/\\1/p' "$2" | tr ',' '\\n' |
while read -r name || [ -n "$name" ]; do
  printf '%s{{"file": %s, "configured": true, "ignored": false, "typed": true, ' "$sep" "$name" \\
    >> '{answer}'
  printf '"parser": "x", "rules": []}}' >> '{answer}'
  sep=,
done
echo ']}}' >> '{answer}'
cat '{answer}'
"""
    helper = script(tmp_path / "main.ts", body)
    environments = Environments(lambda: fake_node.node.executable, fake_node.node.cache)

    def listing(where: Path) -> list[str]:
        return sorted(p.relative_to(where).as_posix() for p in where.rglob("*") if p.is_file())

    return Fixture(TypeScriptFixer(environments, pins, listing, helper=helper), root, fake_node)


@pytest.fixture
def fixture(fake_node: FakeNode, tmp_path: Path) -> Fixture:
    return build(fake_node, tmp_path)


def test_configuration_scripts_at_the_component_root_are_formatted() -> None:
    listed = ["app/eslint.config.js", "app/sub/vite.config.ts", "app/src/a.ts", "other.config.js"]
    assert config_scripts(APP, listed) == ("app/eslint.config.js",)
    assert config_scripts(typescript_component("."), listed) == ("other.config.js",)


def test_a_browser_component_formats_its_styles_and_entry_points() -> None:
    web = browser_component(root="app")
    listed = ["app/index.html", "app/src/a.CSS", "app/b.html", "other/c.css", "app/src/a.ts"]
    assert browser_pages(web, listed) == ("app/index.html", "app/src/a.CSS")
    assert browser_pages(APP, listed) == ()


def test_the_copy_leaves_out_installed_packages_links_and_missing_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "index.js").write_text("")
    (root / "a.ts").write_text("a")
    (root / "link.ts").symlink_to("a.ts")
    paths = ["a.ts", "link.ts", "gone.ts", "node_modules/x/index.js"]
    copy_tree(root, tmp_path / "copy", paths)
    assert [p.name for p in (tmp_path / "copy").iterdir()] == ["a.ts"]


def test_fixes_never_overwrite_an_edit_made_meanwhile(tmp_path: Path) -> None:
    copy, root = tmp_path / "copy", tmp_path / "root"
    for directory in (copy, root):
        directory.mkdir()
    (copy / "fixed.ts").write_text("fixed")
    (root / "fixed.ts").write_text("original")
    (copy / "edited.ts").write_text("fixed")
    (root / "edited.ts").write_text("edited meanwhile")
    (copy / "same.ts").write_text("original")
    (root / "same.ts").write_text("original")
    before: dict[str, bytes] = dict.fromkeys(("fixed.ts", "edited.ts", "same.ts"), b"original")
    copy_back(copy, root, before)
    assert [(root / name).read_text() for name in before] == [
        "fixed",
        "edited meanwhile",
        "original",
    ]


def test_files_are_fixed_and_formatted_in_the_working_tree(fixture: Fixture) -> None:
    files = ("app/src/a.ts", "app/tests/a.test.ts", "app/src/gone.ts", "app/src/b.js")
    assert fixture.fixer.fix(APP, fixture.root, files) == []
    directory = fixture.root / "app"
    assert (directory / "src" / "a.ts").read_text() == "export const a = 'tidy';\n// linted\n"
    assert (directory / "tests" / "a.test.ts").read_text() == "export const t = 1;\n// linted\n"
    assert (directory / "eslint.config.js").read_text() == "export default [/* tidy */];\n"
    assert [p.name for p in (directory / "node_modules").iterdir()] == ["left"]
    assert not (directory / "src" / "gone.ts").exists()


def test_findings_that_remain_are_reported(fixture: Fixture) -> None:
    (fixture.root / "app" / "src" / "a.ts").write_text("export const error = 1;\n")
    found = fixture.fixer.fix(APP, fixture.root, ("app/src/a.ts",))
    assert [(d.rule, d.message) for d in found] == [("typescript.lint", "no-x: x")]


def test_configuration_scripts_alone_are_only_formatted(fixture: Fixture) -> None:
    assert fixture.fixer.fix(APP, fixture.root, ()) == []
    directory = fixture.root / "app"
    assert (directory / "eslint.config.js").read_text() == "export default [/* tidy */];\n"
    assert not (directory / "node_modules" / "eslint").exists()


def test_a_component_with_nothing_to_fix_only_checks_its_configuration(
    fixture: Fixture,
) -> None:
    directory = fixture.root / "app"
    (directory / "eslint.config.js").rename(directory / "eslint.config.json")
    found = fixture.fixer.fix(APP, fixture.root, ("app/src/b.js",))
    assert [d.rule for d in found] == ["typescript.config"]
    assert fixture.fake.calls() == []


def test_a_rejected_configuration_fixes_nothing(fixture: Fixture) -> None:
    lock = fixture.root / "app" / "package-lock.json"
    lock.write_text(json.dumps({"lockfileVersion": 1, "packages": {}}))
    found = fixture.fixer.fix(APP, fixture.root, ("app/src/a.ts",))
    assert [d.rule for d in found] == ["typescript.config"]
    assert "messy" in (fixture.root / "app" / "src" / "a.ts").read_text()
    assert fixture.fake.calls() == []


def test_browser_styles_and_entry_points_are_formatted_and_the_rules_required(
    fake_node: FakeNode, tmp_path: Path
) -> None:
    web = browser_component(root="app")
    fixture = build(fake_node, tmp_path, web)
    directory = fixture.root / "app"
    for name in ("src/app.css", "index.html", "other.html"):
        (directory / name).write_text("/* messy */\n")
    found = fixture.fixer.fix(web, fixture.root, ("app/src/a.ts", "app/tests/a.test.ts"))
    assert [(directory / name).read_text() for name in ("src/app.css", "index.html")] == [
        "/* tidy */\n"
    ] * 2
    assert (directory / "other.html").read_text() == "/* messy */\n"
    hooks, promises = "react-hooks/rules-of-hooks, react-hooks/exhaustive-deps", TEST_RULES[0]
    assert [(d.location.path if d.location else None, d.message) for d in found] == [
        ("app/src/a.ts", f"the browser target requires {hooks}, which ESLint does not enable here"),
        (
            "app/tests/a.test.ts",
            f"the browser target requires {promises}, which ESLint does not enable here",
        ),
    ]
