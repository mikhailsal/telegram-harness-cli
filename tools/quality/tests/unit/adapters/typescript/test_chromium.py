import json
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.typescript import chromium
from quality.adapters.typescript.chromium import Chromium, expected_version, system_ldd
from quality.adapters.typescript.lockfile import Lock
from quality.boundedio import ReportError
from quality.diagnostics import Kind, Severity
from quality.testkit.browserfakes import CHROMIUM, FakeChromium, browsers_json
from quality.testkit.nodefakes import script

TEST = {"version": "1.64.1"}


@pytest.fixture
def fake(tmp_path: Path) -> FakeChromium:
    return FakeChromium.create(tmp_path / "fake")


def lock(root: Path, *keys: str) -> Lock:
    return Lock(root, {"": {}, **dict.fromkeys(keys, TEST)})


def write_browsers(directory: Path, text: str = browsers_json()) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "browsers.json").write_text(text)


def test_the_version_comes_from_the_playwright_core_that_playwright_test_loads(
    tmp_path: Path,
) -> None:
    root, installed = tmp_path / "repo", tmp_path / "installed"
    nested = "node_modules/playwright/node_modules/playwright-core"
    keys = ("node_modules/@playwright/test", "node_modules/playwright", nested)
    write_browsers(installed / nested, browsers_json("1.2.3"))
    write_browsers(installed / "node_modules/playwright-core", browsers_json("9.9.9"))
    found = lock(root, *keys, "node_modules/playwright-core")
    assert expected_version(found, root / "apps" / "web", installed) == "1.2.3"


CHAIN = ("node_modules/@playwright/test", "node_modules/playwright", "node_modules/playwright-core")


@pytest.mark.parametrize(
    ("length", "missing"), [(0, "@playwright/test"), (1, "playwright"), (2, "playwright-core")]
)
def test_a_lockfile_without_the_playwright_chain_says_so(
    tmp_path: Path, length: int, missing: str
) -> None:
    message = f"the lockfile installs no {missing} for the component's Playwright Test"
    with pytest.raises(ReportError, match=message):
        expected_version(lock(tmp_path, *CHAIN[:length]), tmp_path, tmp_path / "installed")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (None, "report not found"),
        (json.dumps({"browsers": "chromium"}), "names no chromium-headless-shell version"),
        (json.dumps({"browsers": [{"name": "chromium-headless-shell"}]}), "names no"),
        (json.dumps({"browsers": [1, {"name": "chromium", "browserVersion": "1"}]}), "names no"),
    ],
)
def test_an_unusable_browsers_json_says_so(tmp_path: Path, text: str | None, message: str) -> None:
    installed = tmp_path / "installed"
    if text is not None:
        write_browsers(installed / CHAIN[-1], text)
    with pytest.raises(ReportError, match=message):
        expected_version(lock(tmp_path, *CHAIN), tmp_path, installed)


def test_ldd_is_found_in_the_system_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty, system = tmp_path / "empty", tmp_path / "system"
    monkeypatch.setattr(chromium, "SYSTEM_PATH", (str(empty), str(system)))
    assert system_ldd() is None
    found = script(system / "ldd", "exit 0\n")
    assert system_ldd() == found


def test_a_browser_that_starts_and_matches_is_reported(fake: FakeChromium) -> None:
    calls: list[str] = []
    setup = fake.chromium(calls)
    (online,) = setup.ready("web", CHROMIUM, offline=False)
    (offline,) = setup.ready("web", CHROMIUM, offline=True)
    assert calls == ["install chromium", "locate chromium"]
    assert online == offline
    assert (online.rule, online.severity, online.component) == (
        "typescript.browser-ready",
        Severity.INFO,
        "web",
    )
    assert online.message == (
        f"Chromium {CHROMIUM} starts and matches the locked Playwright: {fake.executable}"
    )


def test_missing_libraries_are_named(fake: FakeChromium) -> None:
    script(fake.ldd, "printf '\\tlibnss3.so => not found\\n\\tlibgbm.so.1 => not found \\n'\n")
    (found,) = fake.chromium([]).ready("web", CHROMIUM, offline=True)
    assert (found.rule, found.kind) == ("typescript.browser-libraries", Kind.ENVIRONMENT)
    assert found.message.endswith("the libraries libnss3.so, libgbm.so.1 are not found")
    assert "deb.deps" in (found.hint or "")


@pytest.mark.parametrize("ldd", ["echo 'not a dynamic executable'\nexit 1\n", None])
def test_without_an_answer_from_ldd_the_browser_must_start(
    fake: FakeChromium, ldd: str | None
) -> None:
    if ldd is not None:
        script(fake.ldd, ldd)
    script(
        fake.executable, "echo 'error while loading shared libraries: libnss3.so' >&2\nexit 127\n"
    )
    (found,) = fake.chromium([], ldd=ldd is not None).ready("web", CHROMIUM, offline=True)
    assert found.rule == "typescript.browser-libraries"
    assert found.message.endswith(
        "does not start: error while loading shared libraries: libnss3.so"
    )


def test_a_silent_failure_reports_its_status(fake: FakeChromium) -> None:
    script(fake.executable, "exit 3\n")
    (found,) = fake.chromium([]).ready("web", CHROMIUM, offline=True)
    assert found.message.endswith("does not start: exit status 3")


def test_a_browser_that_cannot_run_at_all_is_reported(fake: FakeChromium) -> None:
    fake.executable.chmod(0o644)
    (found,) = fake.chromium([], ldd=False).ready("web", CHROMIUM, offline=True)
    assert found.rule == "typescript.browser-libraries"
    assert "does not start: " in found.message


@pytest.mark.parametrize(
    ("output", "shown"), [("echo 'Chrome 155.0.1'\n", "'155.0.1'"), ("", "''")]
)
def test_another_version_than_playwright_expects_fails(
    fake: FakeChromium, output: str, shown: str
) -> None:
    script(fake.executable, output)
    (found,) = fake.chromium([]).ready("web", CHROMIUM, offline=True)
    assert (found.rule, found.kind) == ("typescript.browser-version", Kind.ENVIRONMENT)
    assert found.message == (
        f"the locked Playwright expects Chromium {CHROMIUM}, but the pinned browser is {shown}"
    )


def test_a_browser_that_is_not_installed_is_its_finding(fake: FakeChromium) -> None:
    missing = native.failure("chromium", "chromium 156.0.8078.4 is not installed")

    def locate() -> Path:
        raise missing

    (found,) = Chromium(locate, locate, fake.ldd).ready("web", CHROMIUM, offline=True)
    assert found == missing.diagnostic


def test_a_run_drives_the_located_browser_only_when_it_is_ready(fake: FakeChromium) -> None:
    calls: list[str] = []
    assert fake.chromium(calls).executable("web", CHROMIUM) == fake.executable
    assert calls == ["locate chromium"]
    script(fake.executable, "echo 'Chrome 155.0.1'\n")
    with pytest.raises(native.ToolFailedError) as raised:
        fake.chromium([]).executable("web", CHROMIUM)
    assert raised.value.diagnostic.rule == "typescript.browser-version"
