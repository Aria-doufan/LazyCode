from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}

MAX_OUTPUT_CHARS = 10000

ToolCategory = Literal["read", "write", "command"]


@dataclass
class ToolResult:
    """保存工具输出文本和执行是否失败。"""
    output: str
    is_error: bool = False


class Tool(ABC):
    """定义所有工具共用的接口和元数据。"""
    name: str
    description: str
    params_model: type[BaseModel]
    category: ToolCategory = "read"
    is_concurrency_safe: bool = False
    is_system_tool: bool = False
    should_defer: bool = False

    @property
    def is_read_only(self) -> bool:
        """判断工具是否属于只读类别。"""
        return self.category == "read"


    def get_schema(self) -> dict[str, Any]:
        """把参数模型转换为发送给模型的 schema。"""
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult:
        """执行工具逻辑并返回工具结果。"""
        ...


# --- 流式事件 ---


@dataclass
class TextDelta:
    """表示模型流式输出的一段文本增量。"""
    text: str


@dataclass
class ToolCallStart:
    """表示模型开始调用某个工具。"""
    tool_name: str
    tool_id: str


@dataclass
class ToolCallDelta:
    """表示工具调用 JSON 参数的一段流式片段。"""
    text: str


@dataclass
class ToolCallComplete:
    """表示已经完整解析的工具调用。"""
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ThinkingDelta:
    """表示模型思考内容的一段流式增量。"""
    text: str


@dataclass
class ThinkingComplete:
    """表示完整思考内容及其签名。"""
    thinking: str
    signature: str


@dataclass
class StreamEnd:
    """表示一次流式响应结束及 token 用量。"""
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0


StreamEvent = TextDelta | ThinkingDelta | ThinkingComplete | ToolCallStart | ToolCallDelta | ToolCallComplete | StreamEnd
