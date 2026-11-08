import json
from pathlib import Path

import pytest

from quality.adapters.typescript.lockfile import BROWSER_REQUIRED, Lock
from quality.adapters.typescript.provision import TypeScriptSetup, copy_manifests
from quality.testkit.browserfakes import (
    ALL_PINS,
    FakeChromium,
    install_playwright,
    write_browser_component,
)
from quality.testkit.nodefakes import FakeNode, browser_component, typescript_component
from quality.testkit.nodefakes import write_component as write_node_component

APP = typescript_component()
WEB = browser_component()


@pytest.fixture
def fake_chromium(tmp_path: Path) -> FakeChromium:
    return FakeChromium.create(tmp_path / "fake-chromium")


def setup(fake: FakeNode, calls: list[str], browser: FakeChromium) -> TypeScriptSetup:
    def locate() -> Path:
        calls.append("locate")
        return fake.node.executable

    def install() -> Path:
        calls.append("install")
        return fake.node.executable

    return TypeScriptSetup(locate, install, fake.node.cache, ALL_PINS, browser.chromium(calls))


def write_component(directory: Path) -> Path:
    return write_node_component(directory, ALL_PINS)


def tree(directory: Path) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.is_file() else None for path in directory.rglob("*")}


def test_only_the_lock_and_package_manifests_are_copied(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "packages" / "app").mkdir(parents=True)
    for name in ("package.json", "package-lock.json", ".npmrc", "packages/app/package.json"):
        (root / name).write_text("{}")
    packages: dict[str, dict[str, object]] = {
        name: {} for name in ("", "packages/app", "packages/gone", "node_modules/x")
    }
    target = tmp_path / "copy"
    assert copy_manifests(Lock(root, packages), root, target) == target
    copied = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert copied == ["package-lock.json", "package.json", "packages/app/package.json"]
    nested = copy_manifests(Lock(root / "packages" / "app", {"": {}}), root, tmp_path / "other")
    assert nested == tmp_path / "other" / "packages" / "app"


def test_bootstrap_installs_node_and_downloads_each_lock_once(
    fake_node: FakeNode, fake_chromium: FakeChromium, snapshot: Path
) -> None:
    write_component(snapshot / "app")
    (snapshot / "app" / ".npmrc").write_text("//registry/:_authToken=x\n")
    calls: list[str] = []
    provisioner = setup(fake_node, calls, fake_chromium)
    assert provisioner.provision(APP, snapshot) == []
    assert provisioner.provision(APP, snapshot) == []
    assert calls == ["install", "install"]
    [[cwd, command, *flags]] = fake_node.calls()
    assert command == "ci"
    assert "--offline" not in flags
    assert Path(cwd).name == "app"
    assert Path(cwd) != snapshot / "app"
    assert not (snapshot / "app" / "node_modules").exists()


def test_doctor_locates_node_and_installs_offline(
    fake_node: FakeNode, fake_chromium: FakeChromium, snapshot: Path
) -> None:
    write_component(snapshot / "app")
    fake_node.npm(f"ls -A > '{fake_node.root / 'listing'}'\n")
    calls: list[str] = []
    assert setup(fake_node, calls, fake_chromium).probe(APP, snapshot) == []
    assert calls == ["locate"]
    [[_, _, *flags]] = fake_node.calls()
    assert "--offline" in flags
    listing = (fake_node.root / "listing").read_text().split()
    assert listing == ["package-lock.json", "package.json"]


def test_a_browser_component_gets_its_browser(
    fake_node: FakeNode, fake_chromium: FakeChromium, snapshot: Path
) -> None:
    files = write_browser_component(snapshot / "web")
    before = tree(files)
    fake_node.npm(install_playwright())
    calls: list[str] = []
    provisioner = setup(fake_node, calls, fake_chromium)
    for step in (provisioner.provision, provisioner.probe):
        [ready] = step(WEB, snapshot)
        assert (ready.rule, ready.component) == ("typescript.browser-ready", "web")
    assert calls == ["install", "install chromium", "locate", "locate chromium"]
    assert [call[1] for call in fake_node.calls()] == ["ci", "ci"]
    assert tree(files) == before


def test_a_playwright_made_for_another_chromium_fails(
    fake_node: FakeNode, fake_chromium: FakeChromium, snapshot: Path
) -> None:
    write_browser_component(snapshot / "web")
    fake_node.npm(install_playwright("155.0.1.2"))
    found = setup(fake_node, [], fake_chromium).probe(WEB, snapshot)
    assert [d.rule for d in found] == ["typescript.browser-version"]
    assert "expects Chromium 155.0.1.2" in found[0].message


def test_an_uninstalled_playwright_core_is_reported(
    fake_node: FakeNode, fake_chromium: FakeChromium, snapshot: Path
) -> None:
    write_browser_component(snapshot / "web")
    calls: list[str] = []
    found = setup(fake_node, calls, fake_chromium).provision(WEB, snapshot)
    assert [d.rule for d in found] == ["typescript.browser-version"]
    assert found[0].message.startswith("cannot tell which Chromium the locked Playwright expects")
    assert calls == ["install"]


def test_a_browser_component_without_its_packages_installs_nothing(
    fake_node: FakeNode, fake_chromium: FakeChromium, snapshot: Path
) -> None:
    write_node_component(snapshot / "web")
    calls: list[str] = []
    found = setup(fake_node, calls, fake_chromium).provision(WEB, snapshot)
    assert [d.rule for d in found] == ["typescript.config"] * len(BROWSER_REQUIRED)
    assert "the lockfile does not lock react; add react@19.3.1" in [d.message for d in found]
    assert (calls, fake_node.calls()) == ([], [])


def test_a_node_component_needs_no_browser(
    fake_node: FakeNode, fake_chromium: FakeChromium, snapshot: Path
) -> None:
    write_browser_component(snapshot / "app")
    calls: list[str] = []
    assert setup(fake_node, calls, fake_chromium).provision(APP, snapshot) == []
    assert calls == ["install"]


def test_a_rejected_configuration_installs_nothing(
    fake_node: FakeNode, fake_chromium: FakeChromium, snapshot: Path
) -> None:
    directory = write_component(snapshot / "app")
    (directory / "package-lock.json").write_text(json.dumps({"lockfileVersion": 1}))
    calls: list[str] = []
    found = setup(fake_node, calls, fake_chromium).provision(APP, snapshot)
    assert [d.rule for d in found] == ["typescript.config"]
    assert calls == []
    assert fake_node.calls() == []
