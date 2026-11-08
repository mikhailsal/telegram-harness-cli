from pathlib import Path

import pytest

from quality.adapters.dart.options import check_options, check_test_config, option_files
from quality.testkit.dartfakes import OPTIONS

STRICT = """\
analyzer:
  language: {strict-casts: true, strict-inference: true, strict-raw-types: true}
"""
CHANGES = (
    "changes which tests run or how; only timeout, concurrency, "
    "fold_stack_traces, chain_stack_traces, verbose_trace are accepted"
)
VM_ONLY = "`platforms` names a platform other than vm; the Dart profile supports the Dart VM only"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def options(directory: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    check_options(directory, lambda file, msg: found.append((file, msg)))
    return found


def runner_settings(directory: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    check_test_config(directory, lambda file, msg: found.append((file, msg)))
    return found


def test_options_files_are_found_below_the_component_except_in_tool_directories(
    tmp_path: Path,
) -> None:
    for directory in ("", "b/", "a/", "a/deep/", ".dart_tool/x/", "node_modules/", ".git/"):
        write(tmp_path / f"{directory}analysis_options.yaml", OPTIONS)
    names = [path.relative_to(tmp_path).as_posix() for path in option_files(tmp_path)]
    assert names == [
        "analysis_options.yaml",
        "a/analysis_options.yaml",
        "a/deep/analysis_options.yaml",
        "b/analysis_options.yaml",
    ]


def test_the_strict_options_pass(tmp_path: Path) -> None:
    write(tmp_path / "analysis_options.yaml", OPTIONS)
    write(
        tmp_path / "tool" / "analysis_options.yaml",
        "include: [package:lints/recommended.yaml, extra.yaml]\n"
        + STRICT
        + "  errors: {todo: warning}\nlinter:\n  rules: {prefer_final_locals: true}\n",
    )
    write(tmp_path / "bin" / "analysis_options.yaml", OPTIONS + "linter:\n  rules: [a, b]\n")
    assert options(tmp_path) == []


def test_missing_and_weakened_options_are_reported(tmp_path: Path) -> None:
    assert options(tmp_path) == [
        ("analysis_options.yaml", "an analysis_options.yaml at the component root is required")
    ]
    write(tmp_path / "analysis_options.yaml", "{not yaml")
    weak = (
        "include: package:lints/core.yaml\n"
        "analyzer:\n  language: {strict-casts: true, strict-inference: 'true'}\n"
        "  errors: {todo: ignore, dead_code: info, unused: error}\n"
        "linter:\n  rules: {avoid_print: false, other: true}\n"
    )
    write(tmp_path / "test" / "analysis_options.yaml", weak)
    ((name, message), *rest) = options(tmp_path)
    assert name == "analysis_options.yaml"
    assert message.startswith("the analysis options are unusable: analysis_options.yaml is not")
    assert rest == [
        ("test/analysis_options.yaml", f"`include` must name {'package:lints/recommended.yaml'}"),
        ("test/analysis_options.yaml", "`analyzer.language.strict-inference` must be true"),
        ("test/analysis_options.yaml", "`analyzer.language.strict-raw-types` must be true"),
        (
            "test/analysis_options.yaml",
            "`analyzer.errors` lowers todo to ignore; every diagnostic fails",
        ),
        (
            "test/analysis_options.yaml",
            "`analyzer.errors` lowers dead_code to info; every diagnostic fails",
        ),
        ("test/analysis_options.yaml", "`linter.rules` turns avoid_print off"),
    ]


def test_without_a_test_configuration_nothing_is_checked(tmp_path: Path) -> None:
    assert runner_settings(tmp_path) == []
    write(tmp_path / "dart_test.yaml", "timeout: 2x\nconcurrency: 4\nverbose_trace: true\n")
    assert runner_settings(tmp_path) == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("platforms: [chrome]\n", VM_ONLY),
        ("platforms: [vm, node]\n", VM_ONLY),
        ("platforms: chrome\n", VM_ONLY),
        ("platforms: [vm]\n", f"`platforms` {CHANGES}"),
        ("platforms: vm\n", f"`platforms` {CHANGES}"),
        ("tags: {slow: {skip: true}}\n", f"`tags` {CHANGES}"),
    ],
)
def test_test_settings_that_change_the_run_are_rejected(
    tmp_path: Path, text: str, message: str
) -> None:
    write(tmp_path / "dart_test.yaml", text)
    assert runner_settings(tmp_path) == [("dart_test.yaml", message)]


def test_an_unusable_test_configuration_is_reported(tmp_path: Path) -> None:
    write(tmp_path / "dart_test.yaml", "- a list\n")
    ((name, message),) = runner_settings(tmp_path)
    assert name == "dart_test.yaml"
    assert message == (
        "the test configuration is unusable: dart_test.yaml does not hold a YAML mapping"
    )
