from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from lazycode.tools.base import Tool, ToolResult


class SyntheticOutputParams(BaseModel):
    """定义结构化最终输出的数据载荷。"""
    output: dict[str, Any] | list[Any] | str


class SyntheticOutputTool(Tool):
    """为非交互或协调模式会话返回结构化 JSON 输出。"""
    name = "SyntheticOutput"
    description = (
        "Return structured output in JSON format. "
        "Use this tool to return your final response as structured data "
        "in non-interactive or coordinator mode sessions."
    )
    params_model = SyntheticOutputParams
    category = "read"
    is_concurrency_safe = True
    is_system_tool = True


    def __init__(self, json_schema: dict[str, Any] | None = None) -> None:
        """保存可选 JSON schema 以供校验。"""
        self._json_schema = json_schema


    async def execute(self, params: BaseModel) -> ToolResult:
        """校验输出并返回原始文本或格式化 JSON。"""
        p: SyntheticOutputParams = params  # type: ignore[assignment]

        if self._json_schema is not None:
            error = self._validate_schema(p.output)
            if error:
                return ToolResult(output=f"Output does not match required schema: {error}", is_error=True)

        if isinstance(p.output, str):
            return ToolResult(output=p.output)

        return ToolResult(output=json.dumps(p.output, ensure_ascii=False, indent=2))


    def _validate_schema(self, data: Any) -> str | None:
        """按简化 schema 校验顶层类型和必填字段。"""
        schema = self._json_schema
        if schema is None:
            return None

        if "type" in schema:
            expected_type = schema["type"]
            if expected_type == "object" and not isinstance(data, dict):
                return f"Expected object, got {type(data).__name__}"
            if expected_type == "array" and not isinstance(data, list):
                return f"Expected array, got {type(data).__name__}"
            if expected_type == "string" and not isinstance(data, str):
                return f"Expected string, got {type(data).__name__}"

        if "required" in schema and isinstance(data, dict):
            missing = [k for k in schema["required"] if k not in data]
            if missing:
                return f"Missing required fields: {', '.join(missing)}"

        return None
