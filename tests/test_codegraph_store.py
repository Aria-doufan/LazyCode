from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lazycode.codegraph.models import EdgeRecord, FileRecord, NodeRecord
from lazycode.codegraph import store as store_module
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


def test_store_closes_connection_when_schema_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False
            self.row_factory = None

        def execute(self, statement: str) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    fake_conn = FakeConnection()

    def fake_connect(db_path: Path) -> FakeConnection:
        return fake_conn

    def fail_schema(self: CodeGraphStore) -> None:
        raise RuntimeError("schema failed")

    monkeypatch.setattr(store_module.sqlite3, "connect", fake_connect)
    monkeypatch.setattr(CodeGraphStore, "_create_schema", fail_schema)

    with pytest.raises(RuntimeError, match="schema failed"):
        CodeGraphStore(tmp_path / "codegraph.sqlite")

    assert fake_conn.closed


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


def test_replace_resolved_call_edges_rolls_back_on_insert_failure(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "codegraph.sqlite"
    store = CodeGraphStore(db_path)
    file_record = FileRecord(
        path="pkg/a.py",
        content_hash="abc",
        language="python",
        size=12,
        indexed_at=1.0,
        node_count=3,
    )
    caller = NodeRecord(
        id="pkg/a.py::caller",
        kind="function",
        name="caller",
        qualified_name="caller",
        file_path="pkg/a.py",
        language="python",
        start_line=1,
        end_line=3,
        signature="def caller()",
    )
    old_target = NodeRecord(
        id="pkg/a.py::old_target",
        kind="function",
        name="old_target",
        qualified_name="old_target",
        file_path="pkg/a.py",
        language="python",
        start_line=5,
        end_line=6,
        signature="def old_target()",
    )
    new_target = NodeRecord(
        id="pkg/a.py::new_target",
        kind="function",
        name="new_target",
        qualified_name="new_target",
        file_path="pkg/a.py",
        language="python",
        start_line=8,
        end_line=9,
        signature="def new_target()",
    )
    generated_edge = EdgeRecord(
        source=caller.id,
        target=old_target.id,
        kind="calls",
        line=2,
        col=4,
        metadata={"name": "old_target", "generated_by": "resolve_references"},
    )
    direct_edge = EdgeRecord(
        source=caller.id,
        target=new_target.id,
        kind="calls",
        line=3,
        col=4,
        metadata={"name": "new_target"},
    )

    store.replace_file_graph(
        file_record,
        [caller, old_target, new_target],
        [generated_edge, direct_edge],
        [],
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.replace_resolved_call_edges(
            [
                EdgeRecord(
                    source=caller.id,
                    target="pkg/a.py::missing",
                    kind="calls",
                    line=4,
                    col=4,
                    metadata={"name": "missing", "generated_by": "resolve_references"},
                )
            ]
        )

    rows_after_failure = store._conn.execute(
        "SELECT target FROM edges ORDER BY target"
    ).fetchall()
    assert [str(row["target"]) for row in rows_after_failure] == [
        new_target.id,
        old_target.id,
    ]

    store.replace_resolved_call_edges(
        [
            EdgeRecord(
                source=caller.id,
                target=new_target.id,
                kind="calls",
                line=4,
                col=4,
                metadata={"name": "new_target", "generated_by": "resolve_references"},
            )
        ]
    )

    rows_after_success = store._conn.execute(
        "SELECT target, metadata FROM edges"
    ).fetchall()
    assert sorted(
        (str(row["target"]), str(row["metadata"])) for row in rows_after_success
    ) == [
        (new_target.id, '{"name": "new_target", "generated_by": "resolve_references"}'),
        (new_target.id, '{"name": "new_target"}'),
    ]


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
