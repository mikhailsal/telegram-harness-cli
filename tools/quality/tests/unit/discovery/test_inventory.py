from pathlib import Path

from quality.diagnostics import Severity
from quality.discovery import Inventory, build_inventory
from quality.gitio import TreeEntry, materialize, staged
from quality.policy import Category, Policy, Profile
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, build_policy, classification_toml

OID = "0" * 40
PROJECT = b'[project]\nname = "app"\n'
BASE = {
    "quality.toml": b"",
    "pyproject.toml": PROJECT,
    "src/pkg/__init__.py": b"",
    "tests/test_pkg.py": b"def test_x():\n    pass\n",
    "README.md": b"# App\n",
}


def inventory(policy: Policy, root: Path, files: dict[str, bytes]) -> Inventory:
    entries: list[TreeEntry] = []
    for path, data in sorted(files.items()):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
        entries.append(TreeEntry("100644", OID, len(data), path))
    return build_inventory(policy, root, entries)


def found(result: Inventory) -> list[tuple[str, str]]:
    return [(d.rule, d.message) for d in result.diagnostics]


def test_a_conforming_candidate_has_no_findings(tmp_path: Path) -> None:
    result = inventory(build_policy(), tmp_path, BASE)
    assert found(result) == []
    assert [m.path for m in result.manifests] == ["pyproject.toml"]
    assert len(result.of_component("app")) == len(BASE)
    assert [r.path for r in result.in_category(Category.SOURCE)] == ["src/pkg/__init__.py"]


def test_declared_roots_must_exist(tmp_path: Path) -> None:
    web = ComponentSpec(id="web", root="apps/web", profile="typescript")
    policy = build_policy(ComponentSpec(source=("src", "lib")), web)
    messages = [m for _, m in found(inventory(policy, tmp_path, BASE)) if "root" in m]
    assert messages == [
        "declared root 'lib' has no tracked files",
        "declared root 'apps/web' has no tracked files",
        "declared root 'apps/web/src' has no tracked files",
        "declared root 'apps/web/tests' has no tracked files",
    ]


def test_packages_need_a_component(tmp_path: Path) -> None:
    files = {**BASE, "scripts/pyproject.toml": PROJECT}
    assert found(inventory(build_policy(), tmp_path, files)) == [
        (
            "discovery.manifest-outside-component",
            "python package at 'scripts' has no component",
        ),
    ]


def test_workspace_and_tool_manifests_may_sit_outside_components(tmp_path: Path) -> None:
    api = ComponentSpec(id="api", root="apps/api")
    files = {
        "pyproject.toml": b"[tool.uv.workspace]\nmembers = ['apps/*']\n",
        "package.json": b'{"private": true, "workspaces": ["apps/*"]}',
        "tools/ruff/pyproject.toml": b"[tool.ruff]\n",
        "apps/api/pyproject.toml": PROJECT,
        "apps/api/src/a.py": b"",
        "apps/api/tests/test_a.py": b"",
    }
    result = inventory(build_policy(api), tmp_path, files)
    assert found(result) == []
    assert len(result.manifests) == 4


def test_package_manifests_must_match_the_component_profile(tmp_path: Path) -> None:
    files = {**BASE, "package.json": b'{"name": "x"}'}
    result = inventory(build_policy(), tmp_path, files)
    assert found(result) == [
        ("discovery.profile-mismatch", "typescript manifest in python component"),
    ]
    assert result.diagnostics[0].component == "app"


def test_tool_configuration_in_another_profile_is_allowed(tmp_path: Path) -> None:
    web = ComponentSpec(id="web", profile="typescript", source=("src",))
    files = {"pyproject.toml": b"[tool.ruff]\n", "src/a.ts": b"", "tests/a.test.ts": b""}
    assert found(inventory(build_policy(web), tmp_path, files)) == []


def test_unreadable_manifests_are_reported(tmp_path: Path) -> None:
    result = inventory(build_policy(), tmp_path, {**BASE, "pyproject.toml": b"[project\n"})
    assert [rule for rule, _ in found(result)] == ["discovery.manifest-invalid"]
    assert result.manifests == ()


def test_manifests_in_tests_and_classified_paths_are_fixtures(tmp_path: Path) -> None:
    policy = build_policy(extra=classification_toml("examples/**", "docs"))
    files = {
        **BASE,
        "tests/fixtures/pyproject.toml": b"not toml",
        "examples/demo/pyproject.toml": PROJECT,
    }
    result = inventory(policy, tmp_path, files)
    assert found(result) == []
    assert [m.path for m in result.manifests] == ["pyproject.toml"]


def test_unused_classifications_warn(tmp_path: Path) -> None:
    policy = build_policy(extra=classification_toml("vendor/**", "vendor"))
    result = inventory(policy, tmp_path, BASE)
    assert found(result) == [
        ("discovery.unused-classification", "classification pattern 'vendor/**' matches no file"),
    ]
    assert result.diagnostics[0].severity is Severity.WARNING


def test_symlinks_are_classified_by_name_only(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/alias.py").symlink_to("pkg/__init__.py")
    (tmp_path / "pyproject.toml").symlink_to("README.md")
    entries = [
        TreeEntry("120000", OID, 15, "src/alias.py"),
        TreeEntry("120000", OID, 9, "pyproject.toml"),
    ]
    result = build_inventory(
        build_policy(ComponentSpec(unit=("src",), source=("lib",))), tmp_path, entries
    )
    assert [r.category for r in result.files] == [Category.TESTS, Category.CONFIG]
    assert [rule for rule, _ in found(result)] == [
        "discovery.manifest-invalid",
        "discovery.missing-root",
    ]


def test_submodules_must_be_classified_as_vendor(tmp_path: Path) -> None:
    repo = GitRepo.create(tmp_path / "repo")
    for path, data in BASE.items():
        repo.write(path, data)
    commit = repo.commit_all("root")
    for path in ("vendor/lib", "deps/tool.py"):
        repo.git("update-index", "--add", "--cacheinfo", f"160000,{commit},{path}")
    policy = build_policy(extra=classification_toml("vendor/lib", "vendor"))
    git = repo.repository()
    base = tmp_path / "snapshots"
    base.mkdir()
    with materialize(git, staged(git).tree, base=base) as snapshot:
        result = build_inventory(policy, snapshot.root, snapshot.entries)
    assert found(result) == [
        ("discovery.submodule-unsupported", "submodule is not classified as vendor content"),
    ]
    vendor = next(r for r in result.files if r.path == "vendor/lib")
    assert vendor.category is Category.VENDOR
    assert {m.profile for m in result.manifests} == {Profile.PYTHON}


def test_mixed_roots_and_overlapping_manifests(tmp_path: Path) -> None:
    web = ComponentSpec(id="web", root="apps/web", profile="typescript", source=("src",))
    policy = build_policy(ComponentSpec(), web, extra=classification_toml("package.json", "config"))
    files = {
        **BASE,
        "package.json": b'{"devDependencies": {"prettier": "3.6.2"}}',
        "apps/web/package.json": b'{"name": "web"}',
        "apps/web/pyproject.toml": b"[tool.ruff]\n",
        "apps/web/src/main.ts": b"",
        "apps/web/src/helper.py": b"",
        "apps/web/tests/main.test.ts": b"",
    }
    result = inventory(policy, tmp_path, files)
    owners = {r.path: r.component for r in result.files}
    assert owners["apps/web/src/main.ts"] == "web"
    assert owners["src/pkg/__init__.py"] == "app"
    assert found(result) == [
        ("discovery.language-mismatch", "python file in typescript component 'web'"),
    ]
    assert sorted(m.path for m in result.manifests) == [
        "apps/web/package.json",
        "apps/web/pyproject.toml",
        "pyproject.toml",
    ]
