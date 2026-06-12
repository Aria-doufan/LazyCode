from __future__ import annotations

from pathlib import Path

import pytest

from lazycode.codegraph.models import EdgeRecord, FileRecord, NodeRecord
from lazycode.codegraph.store import CodeGraphStore


def test_node_record_has_stable_storage_fields() -> None:
    node = NodeRecord(
        id="node-1",
        kind="function",
        name="run",
        qualified_name="Service.run",
        file_path="lazycode/service.py",
        language="python",
        start_line=10,
        end_line=20,
        signature="def run(self) -> None",
    )

    assert node.id == "node-1"
    assert node.kind == "function"
    assert node.qualified_name == "Service.run"
    assert node.file_path == "lazycode/service.py"
    assert node.signature == "def run(self) -> None"


def test_edge_record_tracks_location_and_kind() -> None:
    edge = EdgeRecord(
        source="caller",
        target="callee",
        kind="calls",
        line=14,
        col=8,
        metadata={"name": "callee"},
    )

    assert edge.source == "caller"
    assert edge.target == "callee"
    assert edge.kind == "calls"
    assert edge.line == 14
    assert edge.col == 8
    assert edge.metadata == {"name": "callee"}

    with pytest.raises(TypeError):
        edge.metadata["name"] = "other"


def test_store_initializes_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "codegraph.sqlite"
    store = CodeGraphStore(db_path)

    stats = store.stats()

    assert stats == {"files": 0, "nodes": 0, "edges": 0, "unresolved_refs": 0}


def test_store_replaces_file_graph(tmp_path: Path) -> None:
    db_path = tmp_path / "codegraph.sqlite"
    store = CodeGraphStore(db_path)
    file_record = FileRecord(
        path="pkg/a.py",
        content_hash="abc",
        language="python",
        size=12,
        indexed_at=1.0,
        node_count=1,
    )
    node = NodeRecord(
        id="pkg/a.py::hello",
        kind="function",
        name="hello",
        qualified_name="hello",
        file_path="pkg/a.py",
        language="python",
        start_line=1,
        end_line=2,
        signature="def hello()",
    )

    store.replace_file_graph(file_record, [node], [], [])

    assert store.get_file_hash("pkg/a.py") == "abc"
    assert store.get_nodes_by_file("pkg/a.py") == [node]
    assert store.search_nodes("hell") == [node]
