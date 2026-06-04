from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from lazycode.agent import PermissionResponse


_PERM_OPTIONS = [
    ("Yes", PermissionResponse.ALLOW),
    ("Yes, and don't ask again for this pattern", PermissionResponse.ALLOW_ALWAYS),
    ("No", PermissionResponse.DENY),
]


class InlinePermissionWidget(Vertical, can_focus=True):
    """实现Inline 权限界面组件。"""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "deny", "Deny", priority=True),
    ]

    class Responded(Message):


        """封装Responded相关状态和行为。"""
        def __init__(self, response: PermissionResponse) -> None:
            """初始化Responded实例。"""
            super().__init__()
            self.response = response

    def __init__(self, tool_name: str, description: str, **kwargs) -> None:
        """初始化Inline 权限 Widget实例。"""
        super().__init__(id="perm-inline", **kwargs)
        self._tool_name = tool_name
        self._description = description
        self._cursor = 0

    def compose(self) -> ComposeResult:
        """组合compose界面。"""
        yield Static(self._build_content(), id="perm-content")


    def on_mount(self) -> None:
        """处理mount事件。"""
        self.focus()

    def _build_content(self) -> str:
        """构建content。"""
        lines = []
        lines.append(f"\n  [bold yellow]{self._tool_name} command[/bold yellow]\n")
        lines.append(f"    {self._description}\n")
        lines.append("  [dim]This command requires approval[/dim]\n")
        lines.append("  Do you want to proceed?\n")

        for i, (label, _resp) in enumerate(_PERM_OPTIONS):
            if i == self._cursor:
                lines.append(f" [bold cyan]❯[/bold cyan] {i + 1}. [bold]{label}[/bold]")
            else:
                lines.append(f"   {i + 1}. [dim]{label}[/dim]")

        return "\n".join(lines)


    def _refresh(self) -> None:
        """刷新refresh。"""
        content = self.query_one("#perm-content", Static)
        content.update(self._build_content())

    def action_cursor_up(self) -> None:
        """执行cursor up动作。"""
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        """执行cursor down动作。"""
        if self._cursor < len(_PERM_OPTIONS) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        """执行select动作。"""
        _, response = _PERM_OPTIONS[self._cursor]
        self.post_message(self.Responded(response))


    def action_deny(self) -> None:
        """执行deny动作。"""
        self.post_message(self.Responded(PermissionResponse.DENY))
