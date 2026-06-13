from __future__ import annotations

from pathlib import Path
import time

import pytest

from lazycode.tools import codegraph as codegraph_tools
from lazycode.tools.codegraph import (
    CodeGraphCallersTool,
    CodeGraphExploreTool,
    CodeGraphIndexParams,
    CodeGraphIndexTool,
    CodeGraphNodeParams,
    CodeGraphNodeTool,
)


@pytest.mark.asyncio
async def test_codegraph_index_tool_builds_index(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text("def run():\n    pass\n", encoding="utf-8")

    tool = CodeGraphIndexTool(default_project_root=tmp_path)
    result = await tool.execute(CodeGraphIndexParams(project_path=""))

    assert not result.is_error
    assert "indexed=1" in result.output
    assert (tmp_path / ".lazycode" / "codegraph.sqlite").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("project_path", ["..", "{sibling}"])
async def test_codegraph_index_tool_rejects_paths_outside_default_root(
    tmp_path: Path,
    project_path: str,
) -> None:
    default_root = tmp_path / "root"
    default_root.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    requested = str(sibling) if project_path == "{sibling}" else project_path

    tool = CodeGraphIndexTool(default_project_root=default_root)
    result = await tool.execute(CodeGraphIndexParams(project_path=requested))

    assert result.is_error
    assert "inside default project root" in result.output
    assert not (tmp_path / ".lazycode").exists()
    assert not (sibling / ".lazycode").exists()


@pytest.mark.asyncio
async def test_codegraph_explore_tool_returns_error_when_store_construction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".lazycode" / "codegraph.sqlite"
    db_path.parent.mkdir()
    db_path.write_text("not a sqlite database", encoding="utf-8")

    def raise_store_error(db_path: Path) -> object:
        raise PermissionError("store denied")

    monkeypatch.setattr(codegraph_tools, "CodeGraphStore", raise_store_error)
    tool = CodeGraphExploreTool(default_project_root=tmp_path)

    result = await tool.execute(tool.params_model(query="run"))

    assert result.is_error
    assert "Error exploring CodeGraph index" in result.output
    assert "store denied" in result.output


@pytest.mark.asyncio
async def test_codegraph_explore_tool_requires_existing_index(tmp_path: Path) -> None:
    tool = CodeGraphExploreTool(default_project_root=tmp_path)

    result = await tool.execute(tool.params_model(query="run"))

    assert not result.is_error
    assert "No CodeGraph index found" in result.output


@pytest.mark.asyncio
async def test_explore_warns_when_indexed_file_changed(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text("def run():\n    return 'old'\n", encoding="utf-8")
    await CodeGraphIndexTool(default_project_root=tmp_path).execute(CodeGraphIndexParams(project_path=""))
    time.sleep(0.01)
    source.write_text("def run():\n    return 'new'\n", encoding="utf-8")
    tool = CodeGraphExploreTool(default_project_root=tmp_path)

    result = await tool.execute(tool.params_model(query="run"))

    assert not result.is_error
    assert "Index may be stale" in result.output
    assert "pkg/service.py" in result.output


@pytest.mark.asyncio
async def test_explore_warns_when_indexed_file_deleted(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text("def run():\n    return 'old'\n", encoding="utf-8")
    await CodeGraphIndexTool(default_project_root=tmp_path).execute(CodeGraphIndexParams(project_path=""))
    source.unlink()
    tool = CodeGraphExploreTool(default_project_root=tmp_path)

    result = await tool.execute(tool.params_model(query="run"))

    assert not result.is_error
    assert "Index may be stale" in result.output
    assert "pkg/service.py" in result.output


@pytest.mark.asyncio
async def test_node_warns_when_indexed_file_replaced_by_directory(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text("def run():\n    return 'old'\n", encoding="utf-8")
    await CodeGraphIndexTool(default_project_root=tmp_path).execute(CodeGraphIndexParams(project_path=""))
    source.unlink()
    source.mkdir()
    tool = CodeGraphNodeTool(default_project_root=tmp_path)

    result = await tool.execute(CodeGraphNodeParams(symbol="run"))

    assert not result.is_error
    assert "Index may be stale" in result.output
    assert "pkg/service.py" in result.output


@pytest.mark.asyncio
async def test_codegraph_node_tool_reads_indexed_symbol(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    index_tool = CodeGraphIndexTool(default_project_root=tmp_path)
    await index_tool.execute(CodeGraphIndexParams(project_path=""))

    tool = CodeGraphNodeTool(default_project_root=tmp_path)
    result = await tool.execute(CodeGraphNodeParams(symbol="run"))

    assert not result.is_error
    assert "pkg/service.py:1-2 function run" in result.output
    assert "1\tdef run():" in result.output


@pytest.mark.asyncio
async def test_codegraph_callers_tool_lists_callers(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "service.py"
    source.parent.mkdir()
    source.write_text(
        "def helper():\n"
        "    pass\n\n"
        "def run():\n"
        "    helper()\n",
        encoding="utf-8",
    )
    index_tool = CodeGraphIndexTool(default_project_root=tmp_path)
    await index_tool.execute(CodeGraphIndexParams(project_path=""))

    tool = CodeGraphCallersTool(default_project_root=tmp_path)
    result = await tool.execute(tool.params_model(symbol="helper"))

    assert not result.is_error
    assert "pkg/service.py:4-5 function run" in result.output


from lazycode.tools import create_default_registry


def test_default_registry_includes_deferred_codegraph_tools() -> None:
    registry = create_default_registry()

    assert "CodeGraphIndex" in registry.get_deferred_tool_names()
    assert "CodeGraphExplore" in registry.get_deferred_tool_names()
    assert "CodeGraphNode" in registry.get_deferred_tool_names()
    assert "CodeGraphCallers" in registry.get_deferred_tool_names()
