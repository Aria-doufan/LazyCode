from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from lazycode.codegraph.context import build_explore_context, render_node_source
from lazycode.codegraph.indexer import CodeGraphIndexer, default_db_path
from lazycode.codegraph.models import NodeRecord
from lazycode.codegraph.store import CodeGraphStore
from lazycode.tools.base import Tool, ToolResult


class CodeGraphIndexParams(BaseModel):
    project_path: str = Field(default="", description="Project root to index. Defaults to the current project.")


class CodeGraphExploreParams(BaseModel):
    query: str = Field(description="Symbol, file path, or phrase to explore in the CodeGraph index")
    max_nodes: int = Field(default=8, description="Maximum number of matching nodes to include")
    project_path: str = Field(default="", description="Project root containing the CodeGraph index")


class CodeGraphNodeParams(BaseModel):
    symbol: str = Field(description="Symbol name, qualified name, or path fragment to inspect")
    project_path: str = Field(default="", description="Project root containing the CodeGraph index")


class CodeGraphCallersParams(BaseModel):
    symbol: str = Field(description="Function symbol to find callers for")
    limit: int = Field(default=20, description="Maximum number of callers to return")
    project_path: str = Field(default="", description="Project root containing the CodeGraph index")


class CodeGraphIndexTool(Tool):
    name = "CodeGraphIndex"
    description = "Build or refresh the CodeGraph index for a project."
    params_model = CodeGraphIndexParams
    category = "command"
    should_defer = True

    def __init__(self, default_project_root: Path | str | None = None) -> None:
        self.default_project_root = Path(default_project_root) if default_project_root else Path.cwd()

    async def execute(self, params: CodeGraphIndexParams) -> ToolResult:
        project_root = _project_root(self.default_project_root, params.project_path)
        if isinstance(project_root, ToolResult):
            return project_root

        store = CodeGraphStore(default_db_path(project_root))
        try:
            result = CodeGraphIndexer(project_root, store).index_all()
            return ToolResult(
                output=(
                    "CodeGraph index updated: "
                    f"indexed={result['indexed']} "
                    f"skipped={result['skipped']} "
                    f"removed={result['removed']}"
                )
            )
        except Exception as exc:
            return ToolResult(output=f"Error updating CodeGraph index: {exc}", is_error=True)
        finally:
            store.close()


class CodeGraphExploreTool(Tool):
    name = "CodeGraphExplore"
    description = "Search indexed symbols and return relevant CodeGraph source context."
    params_model = CodeGraphExploreParams
    category = "read"
    is_concurrency_safe = True
    should_defer = True

    def __init__(self, default_project_root: Path | str | None = None) -> None:
        self.default_project_root = Path(default_project_root) if default_project_root else Path.cwd()

    async def execute(self, params: CodeGraphExploreParams) -> ToolResult:
        project_root = _project_root(self.default_project_root, params.project_path)
        if isinstance(project_root, ToolResult):
            return project_root
        if not default_db_path(project_root).exists():
            return ToolResult(output=_NO_INDEX_MESSAGE)

        store = CodeGraphStore(default_db_path(project_root))
        try:
            return ToolResult(
                output=build_explore_context(
                    project_root,
                    store,
                    params.query,
                    max_nodes=params.max_nodes,
                )
            )
        except Exception as exc:
            return ToolResult(output=f"Error exploring CodeGraph index: {exc}", is_error=True)
        finally:
            store.close()


class CodeGraphNodeTool(Tool):
    name = "CodeGraphNode"
    description = "Return source for matching indexed CodeGraph symbols."
    params_model = CodeGraphNodeParams
    category = "read"
    is_concurrency_safe = True
    should_defer = True

    def __init__(self, default_project_root: Path | str | None = None) -> None:
        self.default_project_root = Path(default_project_root) if default_project_root else Path.cwd()

    async def execute(self, params: CodeGraphNodeParams) -> ToolResult:
        project_root = _project_root(self.default_project_root, params.project_path)
        if isinstance(project_root, ToolResult):
            return project_root
        if not default_db_path(project_root).exists():
            return ToolResult(output=_NO_INDEX_MESSAGE)

        store = CodeGraphStore(default_db_path(project_root))
        try:
            nodes = store.search_nodes(params.symbol, limit=5)
            if not nodes:
                return ToolResult(output=f"No indexed symbols matched: {params.symbol}")
            return ToolResult(output=_render_nodes(project_root, nodes))
        except Exception as exc:
            return ToolResult(output=f"Error reading CodeGraph node: {exc}", is_error=True)
        finally:
            store.close()


class CodeGraphCallersTool(Tool):
    name = "CodeGraphCallers"
    description = "List indexed callers of a function symbol."
    params_model = CodeGraphCallersParams
    category = "read"
    is_concurrency_safe = True
    should_defer = True

    def __init__(self, default_project_root: Path | str | None = None) -> None:
        self.default_project_root = Path(default_project_root) if default_project_root else Path.cwd()

    async def execute(self, params: CodeGraphCallersParams) -> ToolResult:
        project_root = _project_root(self.default_project_root, params.project_path)
        if isinstance(project_root, ToolResult):
            return project_root
        if not default_db_path(project_root).exists():
            return ToolResult(output=_NO_INDEX_MESSAGE)

        store = CodeGraphStore(default_db_path(project_root))
        try:
            callers = store.get_callers(params.symbol, limit=params.limit)
            if not callers:
                return ToolResult(output=f"No callers found for {params.symbol}.")
            lines = [f"Callers of {params.symbol}:"]
            lines.extend(f"- {_format_node_location(node)}" for node in callers)
            return ToolResult(output="\n".join(lines))
        except Exception as exc:
            return ToolResult(output=f"Error reading CodeGraph callers: {exc}", is_error=True)
        finally:
            store.close()


_NO_INDEX_MESSAGE = "No CodeGraph index found. Run CodeGraphIndex first, then retry this query."


def _project_root(default_root: Path, requested: str) -> Path | ToolResult:
    root = Path(requested) if requested else default_root
    if not root.is_absolute():
        root = default_root / root
    try:
        root = root.resolve()
    except OSError as exc:
        return ToolResult(output=f"Error resolving project path: {exc}", is_error=True)
    if not root.exists():
        return ToolResult(output=f"Error: project path not found: {root}", is_error=True)
    if not root.is_dir():
        return ToolResult(output=f"Error: project path is not a directory: {root}", is_error=True)
    return root


def _render_nodes(project_root: Path, nodes: list[NodeRecord]) -> str:
    sections: list[str] = []
    for node in nodes:
        sections.extend(
            [
                _format_node_location(node),
                "```",
                render_node_source(project_root, node),
                "```",
            ]
        )
    return "\n".join(sections)


def _format_node_location(node: NodeRecord) -> str:
    return f"{node.file_path}:{node.start_line}-{node.end_line} {node.kind} {node.qualified_name}"
