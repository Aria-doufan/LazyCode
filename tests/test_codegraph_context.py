from __future__ import annotations

from pathlib import Path

from lazycode.codegraph.context import build_explore_context, render_node_source
from lazycode.codegraph.indexer import CodeGraphIndexer
from lazycode.codegraph.models import NodeRecord
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


def test_render_node_source_respects_pep263_source_encoding(tmp_path: Path) -> None:
    source = tmp_path / "latin1_module.py"
    source.write_bytes(
        "# coding: latin-1\n\ndef greet():\n    return 'caf\xe9'\n".encode("latin-1")
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    CodeGraphIndexer(tmp_path, store).index_all()
    node = store.search_nodes("greet")[0]

    output = render_node_source(tmp_path, node)

    assert "3\tdef greet():" in output
    assert "4\t    return 'café'" in output


def test_build_explore_context_includes_symbols_and_source(tmp_path: Path) -> None:
    store = _index_project(tmp_path)

    output = build_explore_context(tmp_path, store, "Service run", max_nodes=4)

    assert "# CodeGraph Explore: Service run" in output
    assert "pkg/service.py:2-3 method Service.run" in output
    assert "def run(self)" in output


def test_render_node_source_rejects_paths_escaping_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET_OUTSIDE_CONTENT\n", encoding="utf-8")
    node = NodeRecord(
        id="escape",
        kind="function",
        name="outside",
        qualified_name="outside",
        file_path="../outside.py",
        language="python",
        start_line=1,
        end_line=1,
    )

    output = render_node_source(project_root, node)

    assert output == "Error: source path escapes project root: ../outside.py"
    assert "SECRET_OUTSIDE_CONTENT" not in output


def test_render_node_source_truncates_within_max_chars(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def example():\n    return 'long enough to truncate'\n", encoding="utf-8")
    node = NodeRecord(
        id="example",
        kind="function",
        name="example",
        qualified_name="example",
        file_path="module.py",
        language="python",
        start_line=1,
        end_line=2,
    )

    output = render_node_source(tmp_path, node, max_chars=25)

    assert len(output) <= 25
    assert "<truncated>" in output


def test_build_explore_context_deduplicates_duplicate_source_ranges(
    tmp_path: Path,
) -> None:
    source = tmp_path / "module.py"
    source.write_text("def duplicate():\n    return 'ok'\n", encoding="utf-8")
    nodes = [
        NodeRecord(
            id="duplicate-1",
            kind="function",
            name="duplicate",
            qualified_name="duplicate",
            file_path="module.py",
            language="python",
            start_line=1,
            end_line=2,
        ),
        NodeRecord(
            id="duplicate-2",
            kind="function",
            name="duplicate",
            qualified_name="duplicate",
            file_path="module.py",
            language="python",
            start_line=1,
            end_line=2,
        ),
    ]

    class FakeStore:
        def all_nodes(self) -> list[NodeRecord]:
            return nodes

    output = build_explore_context(tmp_path, FakeStore(), "duplicate", max_nodes=4)

    assert output.count("1\tdef duplicate():") == 1


def test_explore_prefers_nodes_matching_multiple_query_terms(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "auth.py").write_text(
        "class AuthService:\n"
        "    def login(self):\n"
        "        return True\n",
        encoding="utf-8",
    )
    (pkg / "misc.py").write_text("def login():\n    return True\n", encoding="utf-8")
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    CodeGraphIndexer(tmp_path, store).index_all()

    output = build_explore_context(tmp_path, store, "AuthService login", max_nodes=1)

    assert "pkg/auth.py:2-3 method AuthService.login" in output
    assert "pkg/misc.py" not in output


def test_explore_ranks_separate_query_terms_instead_of_whole_phrase(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "auth.py").write_text(
        "class AuthService:\n"
        "    def login(self):\n"
        "        return True\n",
        encoding="utf-8",
    )
    (pkg / "misc.py").write_text("def login():\n    return True\n", encoding="utf-8")
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    CodeGraphIndexer(tmp_path, store).index_all()

    output = build_explore_context(tmp_path, store, "auth login", max_nodes=1)

    assert "pkg/auth.py:2-3 method AuthService.login" in output
    assert "pkg/misc.py" not in output
