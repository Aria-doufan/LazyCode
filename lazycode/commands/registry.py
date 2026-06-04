from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol


class CommandType(str, Enum):
    """封装命令 Type相关状态和行为。"""
    LOCAL = "local"
    LOCAL_UI = "local_ui"
    PROMPT = "prompt"


class UIController(Protocol):
    """定义命令处理器可调用的 UI 控制接口。"""

    def add_system_message(self, text: str) -> None:
        """添加系统消息到界面。"""
        ...


    def send_user_message(self, text: str) -> None:
        """发送user 消息。"""
        ...
    def set_plan_mode(self, enabled: bool) -> None:
        """设置plan mode。"""
        ...
    def get_token_count(self) -> tuple[int, int]:
        """获取token count。"""
        ...
    def refresh_status(self) -> None:
        """刷新状态。"""
        ...


@dataclass
class CommandContext:
    """封装命令 上下文相关状态和行为。"""
    args: str
    agent: Any
    conversation: Any
    session: Any
    session_manager: Any
    memory_manager: Any
    ui: UIController
    config: Any


CommandHandler = Callable[[CommandContext], Awaitable[None]]


@dataclass
class Command:
    """封装命令相关状态和行为。"""
    name: str
    description: str
    type: CommandType
    handler: CommandHandler
    aliases: list[str] = field(default_factory=list)
    usage: str = ""
    arg_prompt: str = ""
    hidden: bool = False


class CommandRegistry:


    """维护命令注册表。"""
    def __init__(self) -> None:
        """初始化命令 注册表实例。"""
        self._commands: dict[str, Command] = {}
        self._alias_map: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def register(self, command: Command) -> None:
        """注册register。"""
        async with self._lock:
            if command.name in self._commands or command.name in self._alias_map:
                raise ValueError(
                    f"Command name '{command.name}' conflicts with an existing command or alias"
                )
            for alias in command.aliases:
                if alias in self._alias_map or alias in self._commands:
                    raise ValueError(
                        f"Alias '{alias}' conflicts with an existing command or alias"
                    )
            self._commands[command.name] = command
            for alias in command.aliases:
                self._alias_map[alias] = command.name

    def register_sync(self, command: Command) -> None:
        """注册sync。"""
        if command.name in self._commands or command.name in self._alias_map:
            raise ValueError(
                f"Command name '{command.name}' conflicts with an existing command or alias"
            )
        for alias in command.aliases:
            if alias in self._alias_map or alias in self._commands:
                raise ValueError(
                    f"Alias '{alias}' conflicts with an existing command or alias"
                )
        self._commands[command.name] = command
        for alias in command.aliases:
            self._alias_map[alias] = command.name


    def find(self, name: str) -> Command | None:
        """查找find。"""
        if name in self._commands:
            return self._commands[name]
        canon = self._alias_map.get(name)
        if canon:
            return self._commands.get(canon)
        return None


    def list_commands(self) -> list[Command]:
        """列出命令。"""
        return [cmd for cmd in self._commands.values() if not cmd.hidden]
