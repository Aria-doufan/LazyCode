from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lazycode.context.hooks.conditions import ConditionGroup


@dataclass
class Action:
    """封装Action相关状态和行为。"""
    type: str
    command: str = ""
    message: str = ""
    url: str = ""
    method: str = "POST"
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    timeout: int = 30


@dataclass
class ActionResult:
    """表示Action结果。"""
    output: str = ""
    success: bool = True


@dataclass
class Hook:
    """封装hook相关状态和行为。"""
    id: str
    event: str
    action: Action
    condition: ConditionGroup | None = None
    reject: bool = False
    once: bool = False
    async_exec: bool = False
    executed: bool = False


    def should_run(self) -> bool:
        """判断是否需要run。"""
        if self.once and self.executed:
            return False
        return True


    def mark_executed(self) -> None:
        """处理mark executed。"""
        self.executed = True


@dataclass
class HookContext:
    """封装hook 上下文相关状态和行为。"""
    event_name: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    message: str = ""
    error: str = ""

    def get_field(self, name: str) -> str:
        """获取field。"""
        if name == "tool":
            return self.tool_name
        if name == "event":
            return self.event_name
        if name.startswith("args."):
            key = name[5:]
            value = self.tool_args.get(key, "")
            return str(value) if value else ""
        return ""

    def expand(self, template: str) -> str:
        """展开expand。"""
        result = template
        result = result.replace("$EVENT", self.event_name)
        result = result.replace("$TOOL_NAME", self.tool_name)
        result = result.replace("$FILE_PATH", self.file_path)
        result = result.replace("$MESSAGE", self.message)
        result = result.replace("$ERROR", self.error)
        for key, value in self.tool_args.items():
            result = result.replace(f"$TOOL_ARGS.{key}", str(value))
        return result


class ToolRejectedError(Exception):
    """表示工具 Rejected异常。"""
    def __init__(self, tool: str, reason: str, hook_id: str) -> None:
        """初始化工具 Rejected Error实例。"""
        self.tool = tool
        self.reason = reason
        self.hook_id = hook_id
        super().__init__(f"Tool '{tool}' rejected by hook '{hook_id}': {reason}")
