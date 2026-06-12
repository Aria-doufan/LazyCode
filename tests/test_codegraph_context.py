from __future__ import annotations

from pathlib import Path

from lazycode.codegraph.context import build_explore_context, render_node_source
from lazycode.codegraph.indexer import CodeGraphIndexer
from lazycode.codegraph.store import CodeGraphStore


def _index_project(tmp_path: Path) -> CodeGraphStore:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return helper()\n\n"
        "def helper():\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    CodeGraphIndexer(tmp_path, store).index_all()
    return store


def test_render_node_source_returns_line_numbered_slice(tmp_path: Path) -> None:
    store = _index_project(tmp_path)
    node = store.search_nodes("run")[0]

    output = render_node_source(tmp_path, node)

    assert "2\t    def run(self):" in output
    assert "3\t        return helper()" in output


def test_build_explore_context_includes_symbols_and_source(tmp_path: Path) -> None:
    store = _index_project(tmp_path)

    output = build_explore_context(tmp_path, store, "Service run", max_nodes=4)

    assert "# CodeGraph Explore: Service run" in output
    assert "pkg/service.py:2-3 method Service.run" in output
    assert "def run(self)" in output
