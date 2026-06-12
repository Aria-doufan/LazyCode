from __future__ import annotations

from lazycode.codegraph.models import EdgeRecord, NodeRecord


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
    assert edge.metadata == {"name": "callee"}
