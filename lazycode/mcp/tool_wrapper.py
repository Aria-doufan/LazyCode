from __future__ import annotations

from typing import Any

from mcp import types as mcp_types
from pydantic import BaseModel, create_model

from lazycode.mcp.client import MCPClient
from lazycode.tools.base import Tool, ToolResult


def _build_params_model(
    tool_name: str, input_schema: dict[str, Any]
) -> type[BaseModel]:
    """把 MCP JSON Schema 转成 Pydantic 模型，让本地工具参数也能统一校验。"""
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for name, prop in properties.items():
        # 未识别类型退回字符串，优先保证工具可调用，再让远端做更细校验。
        py_type = _json_type_to_python(prop.get("type", "string"))
        if name in required:
            field_definitions[name] = (py_type, ...)
        else:
            field_definitions[name] = (py_type | None, None)

    return create_model(f"{tool_name}Params", **field_definitions)


def _json_type_to_python(json_type: str) -> type:
    """映射常见 JSON 类型；这里只做轻量转换，避免重写完整 JSON Schema 引擎。"""
    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    return mapping.get(json_type, str)


def _extract_text(content: list[Any]) -> str:
    """把 MCP 多模态结果压成文本；当前 CLI 只能稳定展示文本内容。"""
    parts: list[str] = []
    for block in content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
        elif isinstance(block, mcp_types.ImageContent):
            parts.append(f"[image: {block.mimeType}]")
        elif isinstance(block, mcp_types.EmbeddedResource):
            resource = block.resource
            if hasattr(resource, "text"):
                parts.append(resource.text)
            else:
                parts.append(f"[binary resource: {resource.uri}]")
    return "\n".join(parts) if parts else "(no output)"


class MCPToolWrapper(Tool):
    """把远端 MCP 工具适配成本地 Tool，复用现有 agent 执行管线。"""
    def __init__(
        self,
        server_name: str,
        tool_def: mcp_types.Tool,
        client: MCPClient,
    ) -> None:
        """保存原始工具定义；调用时仍使用远端原名，展示时使用本地唯一名。"""
        self._server_name = server_name
        self._tool_def = tool_def
        self._client = client
        self.name = f"mcp_{server_name}_{tool_def.name}"
        self.description = tool_def.description or tool_def.name
        self.category = "command"
        self.is_mcp_tool = True
        self.mcp_server_name = server_name
        self.mcp_original_tool_name = tool_def.name
        self.is_concurrency_safe = False
        # MCP 工具可能触发外部服务或副作用，默认延迟加载，减少初始上下文和误调用。
        self.should_defer = True
        self.params_model = _build_params_model(
            tool_def.name, tool_def.inputSchema
        )

    @property
    def mcp_tool_name(self) -> str:
        """处理MCP 工具 name。"""
        return self._tool_def.name


    def get_schema(self) -> dict[str, Any]:
        """返回 MCP 原始 schema，避免 Pydantic 简化后丢失描述和嵌套约束。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._tool_def.inputSchema,
        }


    async def execute(self, params: BaseModel) -> ToolResult:
        """调用远端 MCP 工具；连接断开时先重连，提升长会话可用性。"""
        if not self._client.is_alive:
            try:
                await self._client.connect()
            except Exception as e:
                return ToolResult(
                    output=f"MCP server '{self._server_name}' reconnect failed: {e}",
                    is_error=True,
                )

        try:
            result = await self._client.call_tool(
                self._tool_def.name, params.model_dump(exclude_none=True)
            )
        except Exception as e:
            # 调用失败后标记断开，下一次执行会走重连而不是复用坏会话。
            self._client._alive = False
            return ToolResult(
                output=f"MCP tool call failed: {e}",
                is_error=True,
            )

        text = _extract_text(result.content)
        return ToolResult(output=text, is_error=bool(result.isError))
