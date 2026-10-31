from pathlib import Path

import pytest

from quality.adapters.dart.environment import Sdk
from quality.testkit.dartfakes import FakeDart


@pytest.fixture
def fake_dart(tmp_path: Path) -> FakeDart:
    return FakeDart.create(tmp_path / "fake-dart", tmp_path / "pub-cache")


@pytest.fixture
def sdk(fake_dart: FakeDart) -> Sdk:
    return fake_dart.sdk


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    root.mkdir()
    return root
