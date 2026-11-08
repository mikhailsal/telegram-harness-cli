from pathlib import Path

from quality.adapters.php.gate import settings


def test_the_gate_settings_are_written_once_into_the_work_area(tmp_path: Path) -> None:
    cs_fixer, phpstan = settings(tmp_path)
    assert (cs_fixer.parent, phpstan.parent) == (tmp_path / "php-gate", tmp_path / "php-gate")
    assert "->setRules(['@PER-CS' => true])" in cs_fixer.read_text()
    assert "->setRiskyAllowed(false)" in cs_fixer.read_text()
    text = phpstan.read_text()
    assert "level: max" in text
    assert f"tmpDir: {(tmp_path / 'php-gate' / 'phpstan-tmp').as_posix()}" in text
    phpstan.write_text("kept\n")
    assert settings(tmp_path) == (cs_fixer, phpstan)
    assert phpstan.read_text() == "kept\n"
