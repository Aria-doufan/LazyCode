from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lazycode.tools.base import Tool

if TYPE_CHECKING:
    from lazycode.cache import FileCache


class ToolRegistry:
    """管理工具实例、启用状态和延迟加载发现状态。"""
    def __init__(self) -> None:
        """初始化工具表、禁用集合和已发现集合。"""
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()

    def register(self, tool: Tool) -> None:
        """按工具公开名称注册工具实例。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名称返回工具实例，未知工具返回 None。"""
        return self._tools.get(name)


    def is_enabled(self, name: str) -> bool:
        """判断工具是否已经注册且未被禁用。"""
        return name in self._tools and name not in self._disabled

    def enable(self, name: str) -> None:
        """通过移出禁用集合来启用工具。"""
        self._disabled.discard(name)


    def disable(self, name: str) -> None:
        """按名称禁用已注册工具。"""
        if name in self._tools:
            self._disabled.add(name)

    def enable_all(self) -> None:
        """启用所有已注册工具。"""
        self._disabled.clear()


    def mark_discovered(self, name: str) -> None:
        """标记延迟工具已经被模型发现。"""
        self._discovered.add(name)

    def is_discovered(self, name: str) -> bool:
        """判断延迟工具是否已经被发现。"""
        return name in self._discovered


    def get_deferred_tool_names(self) -> list[str]:
        """列出可用但尚未发现的延迟工具名称。"""
        return [
            name
            for name, tool in self._tools.items()
            if getattr(tool, "should_defer", False)
            and name not in self._discovered
            and name not in self._disabled
        ]

    def search_deferred(
        self, query: str, max_results: int, protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        """搜索延迟工具并返回目标协议对应的 schema。"""
        query_lower = query.lower()
        scored: list[tuple[int, str, Tool]] = []
        for name, tool in self._tools.items():
            if not getattr(tool, "should_defer", False):
                continue
            if name in self._disabled:
                continue
            score = 0
            name_lower = name.lower()
            desc_lower = (tool.description or "").lower()
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            for word in query_lower.split():
                if word in name_lower:
                    score += 3
                if word in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, name, tool))
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for _, _name, tool in scored[:max_results]:
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def find_deferred_by_names(
        self, names: list[str], protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
        """按名称加载延迟工具并返回目标协议对应的 schema。"""
        results: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if not getattr(tool, "should_defer", False):
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results

    def list_tools(self) -> list[Tool]:
        """返回所有已注册的工具实例。"""
        return list(self._tools.values())


    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        """返回当前可用工具的 schema 列表。"""
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if getattr(tool, "should_defer", False) and name not in self._discovered:
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                schemas.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                schemas.append(base)
        return schemas


def create_default_registry(file_cache: FileCache | None = None) -> ToolRegistry:
    """创建包含内置文件、命令和搜索工具的默认注册表。"""
    from lazycode.tools.bash import Bash
    from lazycode.tools.edit_file import EditFile
    from lazycode.tools.glob import Glob
    from lazycode.tools.grep import Grep
    from lazycode.tools.read_file import ReadFile
    from lazycode.tools.write_file import WriteFile

    registry = ToolRegistry()
    registry.register(ReadFile(file_cache=file_cache))
    registry.register(WriteFile(file_cache=file_cache))
    registry.register(EditFile(file_cache=file_cache))
    registry.register(Bash())
    registry.register(Glob())
    registry.register(Grep())

    from lazycode.tools.codegraph import (
        CodeGraphCallersTool,
        CodeGraphExploreTool,
        CodeGraphIndexTool,
        CodeGraphNodeTool,
    )

    registry.register(CodeGraphIndexTool())
    registry.register(CodeGraphExploreTool())
    registry.register(CodeGraphNodeTool())
    registry.register(CodeGraphCallersTool())
    return registry
