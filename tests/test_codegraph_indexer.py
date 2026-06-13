from __future__ import annotations

import hashlib
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


def test_indexer_removes_deleted_files(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text("def removed_symbol():\n    pass\n", encoding="utf-8")
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    first = indexer.index_all()
    source.unlink()
    second = indexer.index_all()

    assert first == {"indexed": 1, "skipped": 0, "removed": 0}
    assert second == {"indexed": 0, "skipped": 0, "removed": 1}
    assert store.search_nodes("removed_symbol") == []


def test_indexer_decodes_declared_python_source_encoding(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "latin1.py"
    source.parent.mkdir()
    source.write_bytes(b"# coding: latin-1\n\ndef caf\xe9():\n    return 'ol\xe9'\n")
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    result = indexer.index_all()

    assert result == {"indexed": 1, "skipped": 0, "removed": 0}
    nodes = store.search_nodes("café")
    assert len(nodes) == 1
    assert nodes[0].file_path == "pkg/latin1.py"


def test_indexer_stores_hash_of_raw_file_bytes(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_bytes(b"def run():\n    pass\n")
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    first_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    assert store.get_file_hash("pkg/service.py") == first_hash

    source.write_bytes(b"def run():\r\n    pass\r\n")
    result = indexer.index_all()
    second_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    assert result == {"indexed": 1, "skipped": 0, "removed": 0}
    assert second_hash != first_hash
    assert store.get_file_hash("pkg/service.py") == second_hash


def test_indexer_does_not_index_skipped_directory_subtrees(tmp_path: Path) -> None:
    skipped = tmp_path / ".venv" / "pkg" / "hidden.py"
    skipped.parent.mkdir(parents=True)
    skipped.write_text("def hidden_from_index():\n    pass\n", encoding="utf-8")
    visible = tmp_path / "pkg" / "visible.py"
    visible.parent.mkdir()
    visible.write_text("def visible_in_index():\n    pass\n", encoding="utf-8")
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    result = indexer.index_all()

    assert result == {"indexed": 1, "skipped": 0, "removed": 0}
    assert store.search_nodes("visible_in_index")[0].file_path == "pkg/visible.py"
    assert store.search_nodes("hidden_from_index") == []


def test_indexer_resolves_cross_file_function_calls(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "helpers.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (pkg / "service.py").write_text(
        "from pkg.helpers import helper\n\ndef run():\n    helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    callers = store.get_callers("helper")

    assert [(n.name, n.file_path) for n in callers] == [("run", "pkg/service.py")]


def test_indexer_does_not_resolve_ambiguous_top_level_function_calls(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "helpers_a.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (pkg / "helpers_b.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (pkg / "service.py").write_text(
        "def run():\n    helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    callers = store.get_callers("helper")

    assert all(caller.name != "run" for caller in callers)


def test_indexer_does_not_resolve_calls_after_star_import_to_unrelated_helper(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "helpers.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (pkg / "service.py").write_text(
        "from plugin import *\n\ndef run():\n    helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    callers = store.get_callers("helper")

    assert all(caller.name != "run" for caller in callers)


def test_indexer_plain_module_import_shadows_same_named_top_level_function(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "service.py").write_text(
        "def helper():\n"
        "    pass\n\n"
        "import helper\n\n"
        "def run():\n"
        "    helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    callers = store.get_callers("helper")

    assert all(caller.name != "run" for caller in callers)


def test_indexer_does_not_resolve_dotted_unresolved_calls_by_last_segment(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "helpers.py").write_text("def send():\n    pass\n", encoding="utf-8")
    (pkg / "service.py").write_text(
        "def run(client):\n    client.send()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()

    assert store.get_callers("send") == []


def test_indexer_does_not_resolve_parameter_shadowed_bare_call(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "service.py").write_text(
        "def helper():\n"
        "    pass\n\n"
        "def run(helper):\n"
        "    helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    callers = store.get_callers("helper")

    assert all(caller.name != "run" for caller in callers)


def test_indexer_does_not_resolve_cross_file_bare_call_to_nested_function(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "nested.py").write_text(
        "def outer():\n"
        "    def helper():\n"
        "        pass\n",
        encoding="utf-8",
    )
    (pkg / "service.py").write_text(
        "def run():\n"
        "    helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    callers = store.get_callers("helper")

    assert all(caller.name != "run" for caller in callers)


def test_indexer_does_not_resolve_bare_method_calls_to_sibling_methods(
    tmp_path: Path,
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "service.py").write_text(
        "class Service:\n"
        "    def helper(self):\n"
        "        pass\n\n"
        "    def run(self):\n"
        "        helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    callers = store.get_callers("helper")

    assert all(caller.qualified_name != "Service.run" for caller in callers)


def test_get_callers_returns_unique_callers_for_repeated_calls(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "def helper():\n"
        "    pass\n\n"
        "def run():\n"
        "    helper()\n"
        "    helper()\n\n"
        "def other():\n"
        "    helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    callers = store.get_callers("helper")

    assert [(n.name, n.file_path) for n in callers] == [
        ("run", "pkg/service.py"),
        ("other", "pkg/service.py"),
    ]


def test_indexer_preserves_cross_file_callers_when_only_target_changes(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    helpers = pkg / "helpers.py"
    helpers.write_text("def helper():\n    pass\n", encoding="utf-8")
    (pkg / "service.py").write_text(
        "from pkg.helpers import helper\n\ndef run():\n    helper()\n",
        encoding="utf-8",
    )
    store = CodeGraphStore(tmp_path / ".lazycode" / "codegraph.sqlite")
    indexer = CodeGraphIndexer(tmp_path, store)

    indexer.index_all()
    assert [(n.name, n.file_path) for n in store.get_callers("helper")] == [
        ("run", "pkg/service.py")
    ]

    helpers.write_text("def helper():\n    return 42\n", encoding="utf-8")
    indexer.index_all()

    assert [(n.name, n.file_path) for n in store.get_callers("helper")] == [
        ("run", "pkg/service.py")
    ]
