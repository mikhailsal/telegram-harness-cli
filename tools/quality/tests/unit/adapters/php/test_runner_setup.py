from pathlib import Path

from quality.adapters.php.runner import BOOTSTRAP, bootstrap, configuration, source_files
from quality.adapters.php.structure import PhpFile
from quality.boundedio import Element, parse_xml
from quality.metrics import FileMetrics
from quality.testing import Suite
from quality.testkit.phpfakes import php_component, suite_request


def php_file(path: str, *, loadable: bool = True, units: int = 1) -> PhpFile:
    return PhpFile(path, loadable, units, FileMetrics(path, (), ()), {})


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_source_files_skip_test_roots_vendor_and_links(tmp_path: Path) -> None:
    component = php_component(source=("src", "web"), integration=("src/it",))
    unit = suite_request(tmp_path, component=component)
    for name in ("src/B.php", "src/Sub/A.php", "web/index.php", "src/it/XTest.php"):
        write(unit.directory / name)
    write(unit.directory / "vendor" / "autoload.php")
    write(unit.directory / "tests" / "ATest.php")
    write(unit.directory / "src" / "notes.md")
    (unit.directory / "src" / "Link.php").symlink_to(unit.directory / "src" / "B.php")
    assert source_files(unit) == ("app/src/B.php", "app/src/Sub/A.php", "app/web/index.php")


def test_the_bootstrap_loads_the_autoloader_and_every_declaring_file(tmp_path: Path) -> None:
    unit = suite_request(tmp_path)
    files = [php_file("app/src/A.php"), php_file("app/src/run.php", loadable=False)]
    files.append(php_file("app/src/it's\\odd.php"))
    path = bootstrap(unit, files)
    assert path == unit.output / BOOTSTRAP
    lines = path.read_text().splitlines()
    assert lines[0] == "<?php"
    assert lines[3:] == [
        f"require_once '{unit.directory / 'vendor' / 'autoload.php'}';",
        f"require_once '{unit.snapshot / 'app/src/A.php'}';",
        f"require_once '{unit.snapshot}/app/src/it\\'s\\\\odd.php';",
    ]


def parsed(path: Path) -> Element:
    return parse_xml(path.read_bytes(), path.name)


def test_a_unit_configuration_filters_every_source_file_with_branch_coverage(
    tmp_path: Path,
) -> None:
    component = php_component(source=("src", "lib"), integration=("tests-it",))
    unit = suite_request(tmp_path, component=component, roots=("tests", "tests/OneTest.php"))
    write(unit.directory / "tests" / "OneTest.php")
    root = parsed(configuration(unit, unit.output / BOOTSTRAP))
    assert root.attrib == {
        "bootstrap": str(unit.output / BOOTSTRAP),
        "cacheDirectory": str(unit.output / "cache"),
        "colors": "false",
        "recordTestRunHistory": "false",
    }
    (suite,) = root.findall("testsuites/testsuite")
    assert suite.attrib == {"name": "unit"}
    assert [(item.tag, item.text, item.attrib) for item in suite] == [
        ("directory", str(unit.directory / "tests"), {"suffix": "Test.php"}),
        ("file", str(unit.directory / "tests" / "OneTest.php"), {}),
    ]
    included = root.findall("source/include/directory")
    assert [(item.text, item.attrib) for item in included] == [
        (str(unit.directory / "src"), {"suffix": ".php"}),
        (str(unit.directory / "lib"), {"suffix": ".php"}),
    ]
    excluded = [item.text for item in root.findall("source/exclude/directory")]
    assert excluded == [
        str(unit.directory / "tests"),
        str(unit.directory / "tests-it"),
        str(unit.directory / "vendor"),
    ]
    (coverage,) = root.findall("coverage")
    assert coverage.attrib == {
        "includeUncoveredFiles": "true",
        "branchCoverage": "true",
        "disableCodeCoverageIgnore": "true",
        "ignoreDeprecatedCodeUnits": "false",
    }


def test_other_suites_are_configured_without_coverage(tmp_path: Path) -> None:
    component = php_component(integration=("tests-it",))
    integration = suite_request(tmp_path, Suite.INTEGRATION, component, roots=("tests-it",))
    write(integration.directory / "tests-it" / "ATest.php")
    root = parsed(configuration(integration, integration.output / BOOTSTRAP))
    assert root.findall("source") == root.findall("coverage") == []
    (suite,) = root.findall("testsuites/testsuite")
    assert suite.attrib == {"name": "integration"}


def test_names_with_markup_are_escaped(tmp_path: Path) -> None:
    unit = suite_request(tmp_path, roots=("t<&>'\"",))
    (unit.directory / "t<&>'\"").mkdir()
    root = parsed(configuration(unit, unit.output / BOOTSTRAP))
    assert root.findall("testsuites/testsuite/directory")[0].text == str(unit.directory / "t<&>'\"")
