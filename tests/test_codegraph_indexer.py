from __future__ import annotations

from pathlib import Path

from lazycode.codegraph.indexer import CodeGraphIndexer, default_db_path
from lazycode.codegraph.store import CodeGraphStore


def test_default_db_path_uses_lazycode_directory(tmp_path: Path) -> None:
    assert default_db_path(tmp_path) == tmp_path / ".lazycode" / "codegraph.sqlite"


def test_indexer_indexes_python_files(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text("def run():\n    pass\n", encoding="utf-8")
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    result = indexer.index_all()

    assert result == {"indexed": 1, "skipped": 0, "removed": 0}
    nodes = store.search_nodes("run")
    assert len(nodes) == 1
    assert nodes[0].file_path == "pkg/service.py"


def test_indexer_skips_unchanged_files(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text("def run():\n    pass\n", encoding="utf-8")
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    first = indexer.index_all()
    second = indexer.index_all()

    assert first == {"indexed": 1, "skipped": 0, "removed": 0}
    assert second == {"indexed": 0, "skipped": 1, "removed": 0}
