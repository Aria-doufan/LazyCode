from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from lazycode.tools.base import Tool, ToolResult

if __import__("typing").TYPE_CHECKING:
    from lazycode.tools import ToolRegistry


class ToolSearchParams(BaseModel):
    """定义延迟工具搜索的查询词和最大结果数。"""
    query: str
    max_results: int = 5


class ToolSearchTool(Tool):
    """搜索或显式加载尚未暴露的延迟工具。"""
    name = "ToolSearch"
    description = (
        "Search for and load additional tools that are not immediately available. "
        "Use query 'select:<name>[,<name>...]' to load specific tools by name, "
        "or provide keywords to search by relevance."
    )
    params_model = ToolSearchParams
    category = "read"
    should_defer = False  # ToolSearch 本身必须始终可用。?


    def __init__(
        self,
        registry: ToolRegistry,
        protocol: str = "anthropic",
    ) -> None:
        """保存工具注册表和目标协议。"""
        self._registry = registry
        self._protocol = protocol


    def get_schema(self) -> dict[str, Any]:
        """返回 ToolSearch 自身的 schema。"""
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }


    async def execute(self, params: BaseModel) -> ToolResult:
        """加载匹配的 schema，并把对应工具标记为已发现。"""
        assert isinstance(params, ToolSearchParams)
        query = params.query
        max_results = params.max_results

        if query.startswith("select:"):
            names = [n.strip() for n in query[7:].split(",")]
            schemas = self._registry.find_deferred_by_names(names, self._protocol)
        else:
            schemas = self._registry.search_deferred(
                query, max_results, self._protocol
            )

        if not schemas:
            deferred_names = self._registry.get_deferred_tool_names()
            return ToolResult(
                output=(
                    f'No matching deferred tools for "{query}". '
                    f'Available: {", ".join(deferred_names)}'
                )
            )

        for s in schemas:
            if "name" in s:
                self._registry.mark_discovered(s["name"])

        return ToolResult(
            output=(
                f"Found {len(schemas)} tool(s). Their full schemas are now loaded:\n\n"
                f"{json.dumps(schemas, indent=2, ensure_ascii=False)}"
            )
        )
