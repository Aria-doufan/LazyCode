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


def test_search_nodes_treats_like_metacharacters_literally(tmp_path: Path) -> None:
    db_path = tmp_path / "codegraph.sqlite"
    store = CodeGraphStore(db_path)
    file_record = FileRecord(
        path="pkg/patterns.py",
        content_hash="abc",
        language="python",
        size=12,
        indexed_at=1.0,
        node_count=4,
    )
    literal_underscore = NodeRecord(
        id="pkg/patterns.py::a_b",
        kind="function",
        name="a_b",
        qualified_name="a_b",
        file_path="pkg/patterns.py",
        language="python",
        start_line=1,
        end_line=2,
        signature="def a_b()",
    )
    wildcard_underscore_match = NodeRecord(
        id="pkg/patterns.py::axb",
        kind="function",
        name="axb",
        qualified_name="axb",
        file_path="pkg/patterns.py",
        language="python",
        start_line=3,
        end_line=4,
        signature="def axb()",
    )
    literal_percent = NodeRecord(
        id="pkg/patterns.py::a%b",
        kind="function",
        name="a%b",
        qualified_name="a%b",
        file_path="pkg/patterns.py",
        language="python",
        start_line=5,
        end_line=6,
        signature="def a_percent_b()",
    )
    wildcard_percent_match = NodeRecord(
        id="pkg/patterns.py::azzzb",
        kind="function",
        name="azzzb",
        qualified_name="azzzb",
        file_path="pkg/patterns.py",
        language="python",
        start_line=7,
        end_line=8,
        signature="def azzzb()",
    )

    store.replace_file_graph(
        file_record,
        [
            literal_underscore,
            wildcard_underscore_match,
            literal_percent,
            wildcard_percent_match,
        ],
        [],
        [],
    )

    assert store.search_nodes("a_b") == [literal_underscore]
    assert store.search_nodes("a%b") == [literal_percent]
