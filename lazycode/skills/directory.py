from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from lazycode.tools import ToolRegistry
from lazycode.tools.base import Tool, ToolResult

log = logging.getLogger(__name__)


def parse_tool_json(path: Path) -> list[dict[str, Any]]:
    """解析工具 JSON。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Failed to parse tool.json at %s: %s", path, e)
        return []

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        log.warning("tool.json at %s must be a JSON array or object", path)
        return []

    return raw


def load_tool_implementation(
    references_dir: Path, tool_name: str
) -> Callable[..., Any] | None:
    """加载工具 implementation。"""
    script = references_dir / f"{tool_name}.py"
    if not script.is_file():
        return None

    module_name = f"lazycode_skill_tool_{tool_name}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        log.warning("Cannot create module spec for %s", script)
        return None

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        log.warning("Failed to load tool implementation %s: %s", script, e)
        return None

    execute_fn = getattr(module, "execute", None)
    if execute_fn is None:
        log.warning("Tool implementation %s has no 'execute' function", script)
        return None

    return execute_fn


class _DynamicParams(BaseModel):
    """保存Dynamic参数。"""
    model_config = {"extra": "allow"}


class SkillCustomTool(Tool):


    """封装skill Custom 工具相关状态和行为。"""
    def __init__(
        self,
        tool_name: str,
        description: str,
        schema: dict[str, Any],
        impl: Callable[..., Any] | None,
    ) -> None:
        """初始化skill Custom 工具实例。"""
        self.name = tool_name
        self.description = description
        self.params_model = _DynamicParams
        self.category = "command"
        self.is_concurrency_safe = False
        self._schema = schema
        self._impl = impl


    def get_schema(self) -> dict[str, Any]:
        """获取schema。"""
        input_schema = self._schema.get("parameters", self._schema.get("input_schema", {}))
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": input_schema,
        }


    async def execute(self, params: BaseModel) -> ToolResult:
        """执行execute。"""
        if self._impl is None:
            return ToolResult(
                output=f"Error: no implementation found for tool '{self.name}'",
                is_error=True,
            )
        try:
            kwargs = params.model_dump()
            import asyncio
            if asyncio.iscoroutinefunction(self._impl):
                result = await self._impl(**kwargs)
            else:
                result = self._impl(**kwargs)
            return ToolResult(output=str(result))
        except Exception as e:
            return ToolResult(output=f"Tool execution error: {e}", is_error=True)


def register_skill_tools(skill_dir: Path, registry: ToolRegistry) -> int:
    """注册skill 工具。"""
    tool_json_path = skill_dir / "tool.json"
    if not tool_json_path.is_file():
        return 0

    schemas = parse_tool_json(tool_json_path)
    references_dir = skill_dir / "references"
    count = 0

    for schema in schemas:
        tool_name = schema.get("name", "")
        if not tool_name:
            log.warning("Skipping tool with no name in %s", tool_json_path)
            continue

        if registry.get(tool_name) is not None:
            log.debug("Tool '%s' already registered, skipping", tool_name)
            continue

        description = schema.get("description", "")
        impl = load_tool_implementation(references_dir, tool_name) if references_dir.is_dir() else None

        if impl is None:
            log.warning("No implementation for tool '%s' in %s", tool_name, references_dir)

        tool = SkillCustomTool(tool_name, description, schema, impl)
        registry.register(tool)
        count += 1

    return count
