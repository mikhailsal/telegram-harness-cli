import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.php.analyzer import TYPES
from quality.adapters.php.composer import Environments
from quality.adapters.php.config import CONFIG
from quality.adapters.php.formatter import FORMAT
from quality.adapters.php.helper import MAIN, Helper, digest
from quality.adapters.php.profile import COMMITTED, RULES, PhpProfile
from quality.adapters.php.runner import CLOVER, JSONL
from quality.adapters.php.runtime import Runtime
from quality.boundedio import JsonValue, ReportError
from quality.coverage import Counter
from quality.diagnostics import Diagnostic
from quality.metrics import Measurements
from quality.testing import ResultsFormat, StaticRequest, Suite, SuiteRequest
from quality.testkit.phpfakes import (
    PINS,
    REQUIRED,
    FakePhp,
    php_component,
    write_component,
    write_helper,
)

APP = php_component()
SOURCES = ("app/src/Calc.php", "app/src/Script.php", "app/src/Shape.php", "app/src/Unused.php")
FILES = (*SOURCES, "app/tests/CalcTest.php")
SAMPLES = Path(__file__).parents[2] / "coverage" / "samples"
RESULTS = """\
for arg in "$@"; do
  case "$arg" in --log-junit=*) echo '<testsuites/>' > "${arg#--log-junit=}" ;; esac
done
"""


@dataclass(frozen=True, slots=True)
class Setup:
    profile: PhpProfile
    fake: FakePhp
    snapshot: Path
    work: Path

    def static(self, files: tuple[str, ...] = FILES) -> StaticRequest:
        return StaticRequest(APP, self.snapshot, self.work, files)

    def suite(self, suite: Suite = Suite.UNIT) -> SuiteRequest:
        output = self.work / f"app-{suite}"
        output.mkdir(parents=True)
        return SuiteRequest(APP, suite, ("tests",), self.snapshot, output, 0.0, self.work)

    def prepared(self) -> Setup:
        assert self.profile.prepare(APP, self.snapshot, self.work) == []
        return self


def structure_answer(fake: FakePhp, snapshot: Path) -> None:
    def entry(path: str, *, loadable: bool = True, units: int = 1) -> dict[str, JsonValue]:
        imports: JsonValue = []
        if path.endswith("CalcTest.php"):
            imports = [
                {"specifier": "App\\Calc", "line": 7, "resolved": str(snapshot / SOURCES[0])}
            ]
        return {
            "file": str(snapshot / path),
            "functions": [],
            "imports": imports,
            "loadable": loadable,
            "units": units,
            "error": None,
        }

    files = [
        entry(SOURCES[0], units=2),
        entry(SOURCES[1], loadable=False),
        entry(SOURCES[2], units=0),
        entry(SOURCES[3]),
        entry(FILES[4]),
    ]
    answer = fake.root / "structure.json"
    answer.write_text(json.dumps({"protocol": 1, "files": files}))
    fake.handle("main.php", f"cat '{answer}'\n")


@pytest.fixture
def setup(tmp_path: Path, fake_php: FakePhp, snapshot: Path) -> Setup:
    write_component(snapshot / "app")
    for name in FILES:
        (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
        (snapshot / name).write_text("<?php\n")
    structure_answer(fake_php, snapshot)
    fake_php.handle("phpstan", 'echo \'{"files": {}, "errors": []}\'\n')
    fake_php.handle("php-cs-fixer", "echo '{\"files\": []}'\n")
    fake_php.handle("phpunit", RESULTS)
    php = fake_php.php
    runtime = Runtime(lambda: php.executable, lambda: php.composer, php.cache, REQUIRED)
    source = write_helper(tmp_path / "helper")
    installed = tmp_path / "helper-cache" / digest(source, php.version) / MAIN
    installed.parent.mkdir(parents=True)
    installed.write_text("<?php\n")
    profile = PhpProfile(Environments(runtime), PINS, Helper(source, tmp_path / "helper-cache"))
    return Setup(profile, fake_php, snapshot, tmp_path / "work")


def test_the_profile_reports_its_rules_and_expects_php_files(setup: Setup) -> None:
    assert setup.profile.rules == RULES
    assert {rule.id for rule in RULES} == {CONFIG.id, FORMAT.id, TYPES.id}
    assert setup.profile.expected("app/src/A.php")
    assert not setup.profile.expected("app/src/A.phpt")


def test_only_an_accepted_configuration_gets_its_packages(setup: Setup) -> None:
    (setup.snapshot / "app" / "composer.lock").unlink()
    found = setup.profile.prepare(APP, setup.snapshot, setup.work)
    assert [diagnostic.rule for diagnostic in found] == [CONFIG.id]
    assert setup.fake.calls() == []
    with pytest.raises(ToolFailedError, match="'app' was not prepared"):
        setup.profile.static(setup.static())


def test_a_committed_vendor_directory_is_a_configuration_finding(setup: Setup) -> None:
    (setup.snapshot / "app" / "vendor").mkdir()
    found = setup.profile.prepare(APP, setup.snapshot, setup.work)
    assert [(item.message, item.location.path if item.location else "") for item in found] == [
        (COMMITTED, "app/vendor")
    ]
    (setup.snapshot / "app" / "vendor").rmdir()
    (setup.snapshot / "app" / "vendor").symlink_to(setup.snapshot)
    assert [item.message for item in setup.profile.prepare(APP, setup.snapshot, setup.work)] == [
        COMMITTED
    ]
    assert setup.fake.calls() == []


def test_composer_findings_stop_the_preparation(setup: Setup) -> None:
    setup.fake.handle("composer", "echo '# General errors' >&2\necho '- bad name' >&2\nexit 2\n")
    found = setup.profile.prepare(APP, setup.snapshot, setup.work)
    assert [item.message for item in found] == ["composer validate: bad name"]
    with pytest.raises(ToolFailedError, match="'app' was not prepared"):
        setup.profile.run(setup.suite())


def test_the_formatter_and_the_analyzer_check_the_php_files(setup: Setup) -> None:
    setup.prepared()
    assert setup.profile.static(setup.static()) == []
    assert setup.fake.scripts()[-2:] == ["php-cs-fixer", "phpstan"]
    format_, analyse = setup.fake.calls()[-2:]
    expected = [str(setup.snapshot / name) for name in FILES]
    assert format_[-5:] == analyse[-5:] == expected
    gate = setup.work / "php-gate"
    assert f"--config={gate / 'cs-fixer.php'}" in format_
    assert f"--configuration={gate / 'phpstan.neon'}" in analyse
    calls = len(setup.fake.calls())
    assert setup.profile.static(setup.static(("app/composer.json",))) == []
    assert len(setup.fake.calls()) == calls


def test_a_failing_formatter_does_not_stop_the_analyzer(setup: Setup) -> None:
    setup.fake.handle("php-cs-fixer", "echo 'crashed' >&2\nexit 16\n")
    setup.fake.handle("phpstan", "echo 'not json'\n")
    found: list[Diagnostic] = setup.prepared().profile.static(setup.static())
    assert [diagnostic.message for diagnostic in found] == [
        "php-cs-fixer: exit status 16: crashed",
        ("phpstan: malformed JSON in phpstan output: Expecting value: line 1 column 1 (char 0)"),
    ]


def test_the_helper_measures_the_php_files(setup: Setup) -> None:
    setup.prepared()
    assert setup.profile.measure(setup.static(())) == Measurements(())
    measured = setup.profile.measure(setup.static())
    assert [metrics.path for metrics in measured.files] == list(FILES)
    assert measured.dependencies["app/tests/CalcTest.php"] == {"app/src/Calc.php": 7}


def relocated(source: Path, target: Path, snapshot: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text().replace("/work/snapshot", str(snapshot)))


def test_a_unit_run_loads_the_declaring_files_and_marks_unloaded_ones(setup: Setup) -> None:
    unit = setup.prepared().suite()
    run = setup.profile.run(unit)
    assert run.results_format is ResultsFormat.JUNIT
    assert run.coverage is None
    bootstrap = (unit.output / "bootstrap.php").read_text()
    assert [name for name in SOURCES if name in bootstrap] == [
        "app/src/Calc.php",
        "app/src/Shape.php",
        "app/src/Unused.php",
    ]
    # Real Clover and JSONL reports of these files, relocated into this snapshot.
    relocated(SAMPLES / "phpunit.clover.xml", unit.output / CLOVER, setup.snapshot)
    for name in ("meta.json", "coverage.jsonl", "tests.jsonl"):
        relocated(SAMPLES / "phpunit-jsonl" / name, unit.output / JSONL / name, setup.snapshot)
    report = setup.profile.coverage((unit.output / CLOVER).read_bytes(), unit)
    assert report.branches_measured
    measured = {path: file.branches_measured for path, file in report.files.items()}
    assert measured == {
        "app/src/Calc.php": True,
        "app/src/Script.php": False,
        "app/src/Shape.php": True,
        "app/src/Unused.php": True,
    }
    assert report.files["app/src/Calc.php"].branches == Counter(6, 7)
    assert report.files["app/src/Calc.php"].lines == Counter(4, 4)


def test_coverage_without_the_jsonl_report_is_invalid_evidence(setup: Setup) -> None:
    unit = setup.prepared().suite()
    setup.profile.run(unit)
    relocated(SAMPLES / "phpunit.clover.xml", unit.output / CLOVER, setup.snapshot)
    shutil.rmtree(unit.output / JSONL, ignore_errors=True)
    with pytest.raises(ReportError, match="report not found or not a regular file"):
        setup.profile.coverage((unit.output / CLOVER).read_bytes(), unit)


def test_other_suites_run_without_coverage_or_the_helper(setup: Setup) -> None:
    integration = setup.prepared().suite(Suite.INTEGRATION)
    run = setup.profile.run(integration)
    assert run.coverage is None
    assert "main.php" not in setup.fake.scripts()
    assert (integration.output / "bootstrap.php").read_text().count("require_once") == 1


def test_a_unit_run_without_source_files_asks_the_helper_nothing(setup: Setup) -> None:
    for name in SOURCES:
        (setup.snapshot / name).unlink()
    unit = setup.prepared().suite()
    setup.profile.run(unit)
    assert "main.php" not in setup.fake.scripts()
