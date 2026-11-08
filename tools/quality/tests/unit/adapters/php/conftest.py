from pathlib import Path

import pytest

from quality.adapters.php.runtime import Php
from quality.testkit.phpfakes import FakePhp


@pytest.fixture
def fake_php(tmp_path: Path) -> FakePhp:
    return FakePhp.create(tmp_path / "fake-php", tmp_path / "composer-cache")


@pytest.fixture
def php(fake_php: FakePhp) -> Php:
    return fake_php.php


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    root.mkdir()
    return root
