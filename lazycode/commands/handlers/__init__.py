from __future__ import annotations

from lazycode.commands.handlers.clear import CLEAR_COMMAND
from lazycode.commands.handlers.compact import COMPACT_COMMAND
from lazycode.commands.handlers.do import DO_COMMAND
from lazycode.commands.handlers.help import HELP_COMMAND
from lazycode.commands.handlers.memory import MEMORY_COMMAND
from lazycode.commands.handlers.permission import PERMISSION_COMMAND
from lazycode.commands.handlers.plan import PLAN_COMMAND
from lazycode.commands.handlers.session import SESSION_COMMAND
from lazycode.commands.handlers.skill import SKILL_COMMAND
from lazycode.commands.handlers.status import STATUS_COMMAND
from lazycode.commands.registry import CommandRegistry


ALL_COMMANDS = [
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    DO_COMMAND,
    SESSION_COMMAND,
    MEMORY_COMMAND,
    PERMISSION_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
]


def register_all_commands(registry: CommandRegistry) -> None:
    """注册all 命令。"""
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)

