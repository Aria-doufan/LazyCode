from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static


class InlineAskUserWidget(Vertical, can_focus=True):
    """实现Inline Ask User界面组件。"""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("tab", "next_q", "Next", priority=True),
        Binding("shift+tab", "prev_q", "Prev", priority=True),
        Binding("space", "toggle", "Toggle", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    class Responded(Message):
        """封装Responded相关状态和行为。"""
        def __init__(self, answers: dict[str, str] | None) -> None:
            """初始化Responded实例。"""
            super().__init__()
            self.answers = answers


    def __init__(self, questions: list[dict], **kwargs) -> None:
        """初始化Inline Ask User Widget实例。"""
        super().__init__(id="askuser-inline", **kwargs)
        self._questions = questions
        self._q_idx = 0
        n = len(questions)
        self._cursors = [0] * n
        self._selected: list[dict[int, bool]] = [{} for _ in range(n)]
        self._others = [""] * n
        self._answered: dict[int, str] = {}
        self._on_submit = False
        self._submit_idx = 0


    def compose(self) -> ComposeResult:
        """组合compose界面。"""
        yield Static(self._build_content(), id="askuser-content")

    def on_mount(self) -> None:
        """处理mount事件。"""
        self.focus()

    def _option_count(self, q_idx: int) -> int:
        """处理option count。"""
        return len(self._questions[q_idx].get("options", [])) + 1  # +1 表示 Other 选项

    def _build_content(self) -> str:
        """构建content。"""
        if self._on_submit:
            return self._render_submit()
        return self._render_question()

    def _render_question(self) -> str:
        """渲染question。"""
        lines = []
        multi = len(self._questions) > 1

        if multi:
            nav = self._render_nav_bar()
            lines.append(nav)
            lines.append("")

        q = self._questions[self._q_idx]
        header = q.get("question", q.get("message", f"Question {self._q_idx + 1}"))
        lines.append(f" [bold color(99)]{header}[/]\n")

        options = q.get("options", [])
        is_multi = q.get("multiSelect", False)
        cursor = self._cursors[self._q_idx]

        for i, opt in enumerate(options):
            label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
            desc = opt.get("description", "") if isinstance(opt, dict) else ""

            prefix = " ❯ " if i == cursor else "   "
            bold = "[bold]" if i == cursor else ""
            end_bold = "[/]" if i == cursor else ""

            if is_multi:
                check = "● " if self._selected[self._q_idx].get(i) else "○ "
            else:
                check = ""

            desc_part = f" — [dim]{desc}[/]" if desc else ""
            lines.append(f"{prefix}{check}{bold}{label}{end_bold}{desc_part}")

        other_idx = len(options)
        prefix = " ❯ " if cursor == other_idx else "   "
        bold = "[bold]" if cursor == other_idx else ""
        end_bold = "[/]" if cursor == other_idx else ""
        lines.append(f"{prefix}{bold}Other{end_bold}")

        if cursor == other_idx:
            text = self._others[self._q_idx]
            display = text if text else "[dim]Type your answer here...[/]"
            lines.append(f"      {display}█")

        if is_multi:
            lines.append("\n      [dim]space to toggle, enter to confirm[/]")
        else:
            lines.append("\n      [dim]enter to confirm[/]")

        return "\n".join(lines)

    def _render_nav_bar(self) -> str:
        """渲染nav bar。"""
        parts = []
        for i, q in enumerate(self._questions):
            header = q.get("header", f"Q{i+1}")
            check = "☑" if i in self._answered else "☐"
            if i == self._q_idx and not self._on_submit:
                parts.append(f"[bold reverse] {header} {check} [/]")
            else:
                parts.append(f" {header} {check} ")
        submit_part = "[bold reverse] ✓ Submit [/]" if self._on_submit else " ✓ Submit "
        parts.append(submit_part)
        left = "[bold]←[/]" if self._q_idx > 0 else "[dim]←[/]"
        right = "[bold]→[/]"
        return f" {left} {'|'.join(parts)} {right}"

    def _render_submit(self) -> str:
        """渲染submit。"""
        lines = ["\n [bold color(99)]Review your answers:[/]\n"]
        for i, q in enumerate(self._questions):
            header = q.get("header", q.get("question", f"Q{i+1}"))
            ans = self._answered.get(i, "")
            if ans:
                lines.append(f"   {header}: {ans}")
            else:
                lines.append(f"   {header}: [dim](not answered)[/]")
        lines.append("")
        for j, label in enumerate(["Submit answers", "Cancel"]):
            if j == self._submit_idx:
                lines.append(f" [bold cyan]❯[/] [bold]{label}[/]")
            else:
                lines.append(f"   [dim]{label}[/]")
        return "\n".join(lines)

    def _refresh(self) -> None:
        """刷新refresh。"""
        self.query_one("#askuser-content", Static).update(self._build_content())

    def _save_current_answer(self) -> None:
        """保存current answer。"""
        q = self._questions[self._q_idx]
        options = q.get("options", [])
        cursor = self._cursors[self._q_idx]
        is_multi = q.get("multiSelect", False)

        if cursor == len(options):  # 选中 Other 选项
            self._answered[self._q_idx] = self._others[self._q_idx] or "Other"
        elif is_multi:
            selected = [
                (opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt))
                for i, opt in enumerate(options)
                if self._selected[self._q_idx].get(i)
            ]
            if not selected:
                opt = options[cursor]
                selected = [opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)]
            self._answered[self._q_idx] = ", ".join(selected)
        else:
            opt = options[cursor]
            self._answered[self._q_idx] = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)

    def action_cursor_up(self) -> None:
        """执行cursor up动作。"""
        if self._on_submit:
            if self._submit_idx > 0:
                self._submit_idx -= 1
                self._refresh()
        else:
            if self._cursors[self._q_idx] > 0:
                self._cursors[self._q_idx] -= 1
                self._refresh()

    def action_cursor_down(self) -> None:
        """执行cursor down动作。"""
        if self._on_submit:
            if self._submit_idx < 1:
                self._submit_idx += 1
                self._refresh()
        else:
            max_c = self._option_count(self._q_idx) - 1
            if self._cursors[self._q_idx] < max_c:
                self._cursors[self._q_idx] += 1
                self._refresh()

    def action_next_q(self) -> None:
        """执行next q动作。"""
        if self._on_submit or len(self._questions) <= 1:
            return
        if self._q_idx < len(self._questions) - 1:
            self._q_idx += 1
        else:
            self._on_submit = True
            self._submit_idx = 0
        self._refresh()

    def action_prev_q(self) -> None:
        """执行prev q动作。"""
        if self._on_submit:
            self._on_submit = False
            self._q_idx = len(self._questions) - 1
            self._refresh()
        elif self._q_idx > 0:
            self._q_idx -= 1
            self._refresh()


    def action_toggle(self) -> None:
        """执行toggle动作。"""
        if self._on_submit:
            return
        q = self._questions[self._q_idx]
        if not q.get("multiSelect", False):
            return
        cursor = self._cursors[self._q_idx]
        options = q.get("options", [])
        if cursor < len(options):
            self._selected[self._q_idx][cursor] = not self._selected[self._q_idx].get(cursor, False)
            self._refresh()

    def action_select(self) -> None:
        """执行select动作。"""
        if self._on_submit:
            if self._submit_idx == 0:
                answers = {}
                for i, q in enumerate(self._questions):
                    key = q.get("question", q.get("message", f"q{i}"))
                    answers[key] = self._answered.get(i, "")
                self.post_message(self.Responded(answers))
            else:
                self.post_message(self.Responded(None))
        else:
            self._save_current_answer()
            if len(self._questions) == 1:
                answers = {}
                q = self._questions[0]
                key = q.get("question", q.get("message", "q0"))
                answers[key] = self._answered.get(0, "")
                self.post_message(self.Responded(answers))
            elif self._q_idx < len(self._questions) - 1:
                self._q_idx += 1
                self._refresh()
            else:
                self._on_submit = True
                self._submit_idx = 0
                self._refresh()


    def action_cancel(self) -> None:
        """执行cancel动作。"""
        self.post_message(self.Responded(None))

    def on_key(self, event) -> None:
        """处理key事件。"""
        if self._on_submit:
            return
        cursor = self._cursors[self._q_idx]
        options = self._questions[self._q_idx].get("options", [])
        if cursor != len(options):  # 只有选中 Other 时才编辑文本
            return
        key = event.key
        if key == "backspace":
            if self._others[self._q_idx]:
                self._others[self._q_idx] = self._others[self._q_idx][:-1]
                self._refresh()
            event.stop()
        elif len(key) == 1 and key.isprintable():
            self._others[self._q_idx] += key
            self._refresh()
            event.stop()
