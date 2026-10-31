import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.adapters.dart.analyzer import TYPES
from quality.adapters.dart.config import CONFIG
from quality.adapters.dart.environment import Environments
from quality.adapters.dart.formatter import FORMAT
from quality.adapters.dart.helper import Helper
from quality.adapters.dart.options import OPTIONS
from quality.adapters.dart.profile import RULES, DartProfile
from quality.adapters.native import ToolFailedError
from quality.coverage import FileCoverage
from quality.diagnostics import Diagnostic
from quality.metrics import Measurements
from quality.testing import ResultsFormat, StaticRequest, Suite, SuiteRequest
from quality.testkit.dartfakes import PINS, FakeDart, dart_component, write_component, write_helper

APP = dart_component()
FILES = ("app/lib/a.dart", "app/lib/src/p.dart", "app/test/a_test.dart")
RESULTS = """\
for arg in "$@"; do
  case "$arg" in --file-reporter=json:*) results="${arg#--file-reporter=json:}" ;; esac
done
echo '{"type":"done","success":true}' > "$results"
"""
LCOV = """\
for arg in "$@"; do case "$arg" in --out=*) echo end_of_record > "${arg#--out=}" ;; esac; done
"""


@dataclass(frozen=True, slots=True)
class Setup:
    profile: DartProfile
    fake: FakeDart
    snapshot: Path
    work: Path

    def static(self, files: tuple[str, ...] = FILES) -> StaticRequest:
        return StaticRequest(APP, self.snapshot, self.work, files)

    def suite(self, suite: Suite = Suite.UNIT) -> SuiteRequest:
        output = self.work / f"app-{suite}"
        output.mkdir(parents=True)
        return SuiteRequest(APP, suite, ("test",), self.snapshot, output, 0.0, self.work)

    def prepared(self) -> Setup:
        assert self.profile.prepare(APP, self.snapshot, self.work) == []
        return self


def structure_answer(fake: FakeDart, directory: Path) -> None:
    def entry(name: str, *, part: bool, edges: tuple[str, ...] = ()) -> dict[str, object]:
        imports = [
            {"specifier": edge, "line": 1, "resolved": str(directory / "lib" / edge)}
            for edge in edges
        ]
        return {
            "file": str(directory / name),
            "part": part,
            "functions": [],
            "imports": imports,
            "error": None,
        }

    files = [
        entry("lib/a.dart", part=False, edges=("src/p.dart",)),
        entry("lib/src/p.dart", part=True),
        entry("test/a_test.dart", part=False),
    ]
    answer = fake.root / "structure.json"
    answer.write_text(json.dumps({"protocol": 1, "files": files}))
    fake.handle("dill", f"cat '{answer}'\n")


@pytest.fixture
def setup(tmp_path: Path, fake_dart: FakeDart, snapshot: Path) -> Setup:
    directory = write_component(snapshot / "app")
    for name in FILES:
        (directory / name.removeprefix("app/")).parent.mkdir(parents=True, exist_ok=True)
        (directory / name.removeprefix("app/")).write_text("")
    structure_answer(fake_dart, directory)
    fake_dart.handle("analyze", 'echo \'{"version": 1, "diagnostics": []}\'\n')
    fake_dart.handle("test", RESULTS)
    fake_dart.handle("run", LCOV)
    environments = Environments(lambda: fake_dart.sdk.executable, fake_dart.sdk.cache)
    helper = Helper(write_helper(tmp_path / "helper"), tmp_path / "helper-cache")
    profile = DartProfile(environments, PINS, helper)
    return Setup(profile, fake_dart, snapshot, tmp_path / "work")


def test_the_profile_reports_its_rules_and_expects_dart_files(setup: Setup) -> None:
    assert setup.profile.rules == RULES
    assert {rule.id for rule in RULES} >= {CONFIG.id, FORMAT.id, TYPES.id}
    assert setup.profile.expected("app/lib/a.dart")
    assert not setup.profile.expected("app/lib/a.dart.txt")


def test_only_an_accepted_configuration_gets_its_packages(setup: Setup) -> None:
    (setup.snapshot / "app" / "analysis_options.yaml").unlink()
    found = setup.profile.prepare(APP, setup.snapshot, setup.work)
    assert [diagnostic.rule for diagnostic in found] == [CONFIG.id]
    (setup.snapshot / "app" / "pubspec.yaml").unlink()
    assert setup.profile.prepare(APP, setup.snapshot, setup.work) != []
    assert setup.fake.commands() == []
    with pytest.raises(ToolFailedError, match="'app' was not prepared"):
        setup.profile.static(setup.static())


def test_the_formatter_and_the_analyzer_check_the_dart_files(setup: Setup) -> None:
    setup.prepared()
    assert setup.profile.static(setup.static()) == []
    assert setup.fake.commands() == ["pub", "format", "analyze"]
    format_, analyze = setup.fake.calls()[1:]
    expected = [str(setup.snapshot / name) for name in FILES]
    settings = [str(setup.snapshot / "app" / name) for name in ("pubspec.yaml", OPTIONS)]
    assert format_[-3:] == expected
    assert analyze[-5:] == [*expected, *settings]
    assert setup.profile.static(setup.static(("app/pubspec.yaml",))) == []
    assert setup.fake.commands() == ["pub", "format", "analyze"]


def test_a_failing_formatter_does_not_stop_the_analyzer(setup: Setup) -> None:
    setup.fake.handle("format", "echo 'crashed' >&2\nexit 255\n")
    setup.fake.handle("analyze", "echo 'not json'\n")
    found: list[Diagnostic] = setup.prepared().profile.static(setup.static())
    assert [diagnostic.message for diagnostic in found] == [
        "dart format: exit status 255: crashed",
        (
            "dart analyze: malformed JSON in dart analyze output: "
            "Expecting value: line 1 column 1 (char 0)"
        ),
    ]


def test_the_helper_measures_the_dart_files(setup: Setup) -> None:
    setup.prepared()
    assert setup.profile.measure(setup.static(())) == Measurements(())
    measured = setup.profile.measure(setup.static())
    assert [metrics.path for metrics in measured.files] == list(FILES)
    assert measured.dependencies["app/lib/a.dart"] == {"app/lib/src/p.dart": 1}


def test_a_unit_run_imports_the_libraries_and_fills_their_parts(setup: Setup) -> None:
    unit = setup.prepared().suite()
    run = setup.profile.run(unit)
    assert run.results_format is ResultsFormat.DART_JSON
    assert run.coverage == unit.output / "lcov.info"
    generated = next((unit.directory / ".dart_tool" / "quality").glob("run-*/*.dart"))
    assert "import 'package:calc/a.dart' as library0;" in generated.read_text()
    assert "p.dart" not in generated.read_text()
    data = f"SF:{unit.directory / 'lib' / 'a.dart'}\nDA:1,1\nend_of_record\n".encode()
    report = setup.profile.coverage(data, unit)
    assert report.branches_measured
    assert report.files["app/lib/src/p.dart"] == FileCoverage(
        "app/lib/src/p.dart", frozenset(), frozenset()
    )
    assert report.files["app/lib/a.dart"].covered == {1}


def test_other_suites_run_without_coverage(setup: Setup) -> None:
    integration = setup.prepared().suite(Suite.INTEGRATION)
    run = setup.profile.run(integration)
    assert run.coverage is None
    assert setup.fake.commands() == ["pub", "test"]
    assert setup.profile.coverage(b"", integration).files == {}
