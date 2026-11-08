from pathlib import Path

import pytest

from quality.adapters.typescript.environment import Node
from quality.testkit.nodefakes import FakeNode


@pytest.fixture
def fake_node(tmp_path: Path) -> FakeNode:
    return FakeNode.create(tmp_path / "fake-node", tmp_path / "npm-cache")


@pytest.fixture
def node(fake_node: FakeNode) -> Node:
    return fake_node.node


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    root.mkdir()
    return root
