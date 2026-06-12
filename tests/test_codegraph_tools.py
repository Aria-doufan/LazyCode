from __future__ import annotations

from pathlib import Path

import pytest

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
async def test_codegraph_explore_tool_requires_existing_index(tmp_path: Path) -> None:
    tool = CodeGraphExploreTool(default_project_root=tmp_path)

    result = await tool.execute(tool.params_model(query="run"))

    assert not result.is_error
    assert "No CodeGraph index found" in result.output


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
