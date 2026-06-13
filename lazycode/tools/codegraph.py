from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from lazycode.codegraph.context import build_explore_context, render_node_source
from lazycode.codegraph.indexer import CodeGraphIndexer, default_db_path, stale_index_paths
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
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of callers to return")
    project_path: str = Field(default="", description="Project root containing the CodeGraph index")


class CodeGraphIndexTool(Tool):
    name = "CodeGraphIndex"
    description = "Build or refresh the CodeGraph index for a project."
    params_model = CodeGraphIndexParams
    category = "command"
    should_defer = True

    def __init__(self, default_project_root: Path | str | None = None) -> None:
        self.default_project_root = Path(default_project_root) if default_project_root else Path.cwd()

    def set_default_project_root(self, default_project_root: Path | str) -> None:
        self.default_project_root = Path(default_project_root)

    async def execute(self, params: CodeGraphIndexParams) -> ToolResult:
        store: CodeGraphStore | None = None
        try:
            project_root = _project_root(self.default_project_root, params.project_path)
            store = CodeGraphStore(default_db_path(project_root))
            result = CodeGraphIndexer(project_root, store).index_all()
            return ToolResult(
                output=(
                    "CodeGraph index updated: "
                    f"indexed={result['indexed']} "
                    f"skipped={result['skipped']} "
                    f"removed={result['removed']}"
                )
            )
        except ValueError as exc:
            return ToolResult(output=f"Error: {exc}", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"Error updating CodeGraph index: {exc}", is_error=True)
        finally:
            if store is not None:
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

    def set_default_project_root(self, default_project_root: Path | str) -> None:
        self.default_project_root = Path(default_project_root)

    async def execute(self, params: CodeGraphExploreParams) -> ToolResult:
        store: CodeGraphStore | None = None
        try:
            project_root = _project_root(self.default_project_root, params.project_path)
            if not default_db_path(project_root).exists():
                return ToolResult(output=_NO_INDEX_MESSAGE)

            store = CodeGraphStore(default_db_path(project_root))
            output = build_explore_context(
                project_root,
                store,
                params.query,
                max_nodes=params.max_nodes,
            )
            return ToolResult(output=_with_stale_warning(project_root, store, output))
        except ValueError as exc:
            return ToolResult(output=f"Error: {exc}", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"Error exploring CodeGraph index: {exc}", is_error=True)
        finally:
            if store is not None:
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

    def set_default_project_root(self, default_project_root: Path | str) -> None:
        self.default_project_root = Path(default_project_root)

    async def execute(self, params: CodeGraphNodeParams) -> ToolResult:
        store: CodeGraphStore | None = None
        try:
            project_root = _project_root(self.default_project_root, params.project_path)
            if not default_db_path(project_root).exists():
                return ToolResult(output=_NO_INDEX_MESSAGE)

            store = CodeGraphStore(default_db_path(project_root))
            nodes = store.search_nodes(params.symbol, limit=5)
            if not nodes:
                output = f"No indexed symbols matched: {params.symbol}"
            else:
                output = _render_nodes(project_root, nodes)
            return ToolResult(output=_with_stale_warning(project_root, store, output))
        except ValueError as exc:
            return ToolResult(output=f"Error: {exc}", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"Error reading CodeGraph node: {exc}", is_error=True)
        finally:
            if store is not None:
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

    def set_default_project_root(self, default_project_root: Path | str) -> None:
        self.default_project_root = Path(default_project_root)

    async def execute(self, params: CodeGraphCallersParams) -> ToolResult:
        store: CodeGraphStore | None = None
        try:
            project_root = _project_root(self.default_project_root, params.project_path)
            if not default_db_path(project_root).exists():
                return ToolResult(output=_NO_INDEX_MESSAGE)

            store = CodeGraphStore(default_db_path(project_root))
            callers = store.get_callers(params.symbol, limit=params.limit)
            if not callers:
                output = f"No callers found for {params.symbol}."
            else:
                lines = [f"Callers of {params.symbol}:"]
                lines.extend(f"- {_format_node_location(node)}" for node in callers)
                output = "\n".join(lines)
            return ToolResult(output=_with_stale_warning(project_root, store, output))
        except ValueError as exc:
            return ToolResult(output=f"Error: {exc}", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"Error reading CodeGraph callers: {exc}", is_error=True)
        finally:
            if store is not None:
                store.close()


_NO_INDEX_MESSAGE = "No CodeGraph index found. Run CodeGraphIndex first, then retry this query."


def _project_root(default_root: Path, requested: str) -> Path:
    try:
        default_root = default_root.resolve()
        root = Path(requested) if requested else default_root
        if not root.is_absolute():
            root = default_root / root
        root = root.resolve()
    except OSError as exc:
        raise ValueError(f"resolving project path failed: {exc}") from exc

    try:
        root.relative_to(default_root)
    except ValueError as exc:
        raise ValueError(f"project path must be inside default project root: {default_root}") from exc

    if not root.exists():
        raise ValueError(f"project path not found: {root}")
    if not root.is_dir():
        raise ValueError(f"project path is not a directory: {root}")
    return root


def _with_stale_warning(project_root: Path, store: CodeGraphStore, output: str) -> str:
    stale_paths = stale_index_paths(project_root, store)
    if not stale_paths:
        return output

    warning_lines = [
        "Index may be stale for these files; run CodeGraphIndex before relying on source slices:",
        *(f"- {path}" for path in stale_paths[:5]),
        "",
        output,
    ]
    return "\n".join(warning_lines)


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
