from __future__ import annotations

import asyncio
import os
import random
import time as _time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TMessage
from textual.widgets import Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from lazycode.agent import (
    Agent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from lazycode.client import (
    AuthenticationError,
    LLMClient,
    LLMError,
    create_client,
)
from lazycode.commands import (
    CommandContext,
    CommandRegistry,
    complete,
    parse_command,
)
from lazycode.commands.completion import CompletionPopup
from lazycode.commands.handlers import register_all_commands
from lazycode.config import MCPServerConfig, ProviderConfig
from lazycode.hooks import HookContext, HookEngine, load_hooks
from lazycode.conversation import ConversationManager, Message
from lazycode.mcp import MCPManager
from lazycode.memory import (
    MemoryManager,
    Session,
    SessionManager,
    build_time_gap_message,
    generate_session_summary,
    load_instructions,
)
from lazycode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from lazycode.agents.loader import AgentLoader
from lazycode.agents.task_manager import TaskManager
from lazycode.agents.trace import TraceManager
from lazycode.agents.notification import inject_task_notifications
from lazycode.commands.handlers.tasks import create_tasks_command
from lazycode.skills.executor import SkillExecutor
from lazycode.skills.loader import SkillLoader
from lazycode.commands.handlers.skill_register import register_skill_commands
from rich.text import Text as RichText
from textual.theme import Theme
from lazycode.cache import FileCache
from lazycode.tools import ToolRegistry, create_default_registry
from lazycode.tools.agent_tool import AgentTool
from lazycode.tools.ask_user import AskUserEvent, AskUserTool
from lazycode.tools.impl.tool_search import ToolSearchTool
from lazycode.tools.load_skill import LoadSkill
from lazycode.worktree.cleanup import start_stale_cleanup_task
from lazycode.worktree.manager import WorktreeManager
from lazycode.commands.handlers.worktree import create_worktree_command

import re

MAX_TRUNCATED_LINES = 20
MAX_AT_REF_BYTES = 10240

_AT_REF_RE = re.compile(r"@([\w./_\-]+(?:\.[\w]+)*)")

_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".lazycode", "build", ".gradle"}


def scan_files_for_at(prefix: str, work_dir: str, limit: int = 10) -> list[str]:
    """扫描文件 for at。"""
    matches: list[str] = []
    normalized_prefix = prefix.replace("\\", "/")
    parts = normalized_prefix.split("/")
    if os.path.isabs(prefix) or any(part == ".." for part in parts):
        return matches

    prefix_dir = "/".join(parts[:-1])
    name_prefix = parts[-1].lower()
    base = os.path.join(work_dir, *parts[:-1]) if prefix_dir else work_dir
    if not os.path.isdir(base):
        return matches
    try:
        for entry in sorted(os.listdir(base)):
            if entry in _SKIP_DIRS or entry.startswith("."):
                continue
            if entry.lower().startswith(name_prefix):
                rel = f"{prefix_dir}/{entry}" if prefix_dir else entry
                if os.path.isdir(os.path.join(base, entry)):
                    rel += "/"
                matches.append(rel)
                if len(matches) >= limit:
                    break
    except OSError:
        pass
    return matches


def expand_at_refs(text: str, work_dir: str) -> str:
    """展开at refs。"""
    def _replace(m: re.Match) -> str:
        """处理replace。"""
        rel_path = m.group(1)
        full_path = os.path.join(work_dir, rel_path)
        if not os.path.isfile(full_path):
            return m.group(0)
        try:
            content = open(full_path, encoding="utf-8", errors="replace").read(MAX_AT_REF_BYTES)
            return f"[File: {rel_path}]\n```\n{content}\n```"
        except Exception:
            return m.group(0)
    return _AT_REF_RE.sub(_replace, text)


class ChatInput(TextArea):
    """处理Chat输入。"""
    BINDINGS = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("shift+enter", "newline", "Newline", priority=True),
        Binding("ctrl+j", "newline", "Newline", priority=True),
        Binding("tab", "complete", "Complete", priority=True),
        Binding("escape", "dismiss_popup", "Dismiss", priority=True),
        Binding("up", "history_prev", "History prev", priority=True),
        Binding("down", "history_next", "History next", priority=True),
    ]

    class Submitted(TMessage):
        """封装Submitted相关状态和行为。"""
        def __init__(self, text: str) -> None:
            """初始化Submitted实例。"""
            super().__init__()
            self.text = text

    class TabComplete(TMessage):
        """封装Tab Complete相关状态和行为。"""
        def __init__(self, text: str) -> None:
            """初始化Tab Complete实例。"""
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        """初始化Chat 输入实例。"""
        super().__init__(**kwargs)
        self.cursor_blink = False
        self._history: list[str] = []
        self._history_index: int = -1
        self._history_draft: str = ""
        self._history_file: Path | None = None

    def load_history(self, work_dir: str) -> None:
        """加载history。"""
        self._history_file = Path(work_dir) / ".lazycode" / "history"
        if self._history_file.exists():
            try:
                lines = self._history_file.read_text(encoding="utf-8").splitlines()
                self._history = [l for l in lines if l.strip()]
            except Exception:
                pass

    def _persist_entry(self, text: str) -> None:
        """处理persist entry。"""
        if self._history_file is None:
            return
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def action_submit(self) -> None:
        """执行submit动作。"""
        text = self.text.strip()
        if text:
            self._history.append(text)
            self._persist_entry(text)
            self._history_index = -1
            self._history_draft = ""
            self.post_message(self.Submitted(text))
            self.clear()

    def action_newline(self) -> None:
        """执行newline动作。"""
        self.insert("\n")

    def action_complete(self) -> None:
        """执行complete动作。"""
        popup = self.app.query_one(CompletionPopup)
        if popup.is_visible:
            popup.select_highlighted()
            self.focus()
            return

        context = self._completion_context(self.text)
        if context is not None and context[0] == "command":
            self.post_message(self.TabComplete(context[1]))
        else:
            self.insert("\t")

    def action_dismiss_popup(self) -> None:
        """执行dismiss popup动作。"""
        self.app.query_one(CompletionPopup).hide()

    def action_history_prev(self) -> None:
        """执行history prev动作。"""
        popup = self.app.query_one(CompletionPopup)
        if popup.is_visible:
            popup.move_selection(-1)
            return
        if not self._history:
            return
        if self._history_index == -1:
            self._history_draft = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return
        self.clear()
        self.insert(self._history[self._history_index])

    def action_history_next(self) -> None:
        """执行history next动作。"""
        popup = self.app.query_one(CompletionPopup)
        if popup.is_visible:
            popup.move_selection(1)
            return
        if self._history_index == -1:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.clear()
            self.insert(self._history[self._history_index])
        else:
            self._history_index = -1
            self.clear()
            self.insert(self._history_draft)

    class CompletionRequest(TMessage):
        """表示补全候选请求。"""
        def __init__(self, kind: str, prefix: str) -> None:
            super().__init__()
            self.kind = kind
            self.prefix = prefix

    @staticmethod
    def _completion_context(text: str) -> tuple[str, str] | None:
        """识别当前输入是否处于命令或文件补全上下文。"""
        if text.startswith("/"):
            if " " in text or "\n" in text:
                return None
            return "command", text

        at_idx = text.rfind("@")
        if at_idx < 0:
            return None
        after = text[at_idx + 1:]
        if " " in after or "\n" in after:
            return None
        return "file", after

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """处理输入变化并请求补全候选。"""
        context = self._completion_context(self.text)
        popup = self.app.query_one(CompletionPopup)
        if context is None:
            popup.hide()
            return
        kind, prefix = context
        self.post_message(self.CompletionRequest(kind, prefix))


COLLAPSIBLE_TOOLS = {"ReadFile", "Glob", "Grep", "ToolSearch"}


def _is_subagent_tool(tool_name: str) -> bool:
    """判断subagent 工具是否成立。"""
    return tool_name == "Agent"


def _tool_title(tool_name: str, arguments: dict[str, Any]) -> str:
    """处理工具 title。"""
    if tool_name == "ReadFile":
        path = os.path.basename(arguments.get("file_path", ""))
        return f"Read {path}" if path else "Read"
    if tool_name == "WriteFile":
        path = os.path.basename(arguments.get("file_path", ""))
        content = arguments.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"Write {path} ({lines} lines)" if path else "Write"
    if tool_name == "EditFile":
        path = os.path.basename(arguments.get("file_path", ""))
        return f"Edit {path}" if path else "Edit"
    if tool_name == "Bash":
        cmd = arguments.get("command", "")
        short = cmd[:50] + "…" if len(cmd) > 50 else cmd
        return f"Bash: {short}" if short else "Bash"
    if tool_name == "Glob":
        return f"Glob: {arguments.get('pattern', '')}"
    if tool_name == "Grep":
        return f"Grep: {arguments.get('pattern', '')}"
    return tool_name


def _format_detail(tool_name: str, arguments: dict[str, Any], output: str) -> str:
    """格式化detail。"""
    parts: list[str] = []

    if tool_name == "Bash":
        parts.append(f"  IN   {arguments.get('command', '')}")
        parts.append("")
        for line in output.splitlines():
            parts.append(f"  OUT  {line}")
    elif tool_name in ("ReadFile", "WriteFile", "EditFile"):
        parts.append(f"  {arguments.get('file_path', '')}")
        parts.append("")
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  … ({total - MAX_TRUNCATED_LINES} more lines)")
    else:
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  … ({total - MAX_TRUNCATED_LINES} more lines)")

    return "\n".join(parts)


class ToolCallBlock(Static, can_focus=True):

    """封装工具 Call Block相关状态和行为。"""
    def __init__(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any) -> None:
        """初始化工具 Call Block实例。"""
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self._arguments = arguments
        self._title = _tool_title(tool_name, arguments)
        self._full_output = ""
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._loading = True
        self._render_loading()

    def _render_loading(self) -> None:
        """渲染loading。"""
        self.update(f"  ⠋ {self._title} …")
        self.add_class("tool-block-loading")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        """设置结果。"""
        self._full_output = output
        self._is_error = is_error
        self._elapsed = elapsed
        self._loading = False
        self._collapsed = True
        self.remove_class("tool-block-loading")
        if is_error:
            self.add_class("tool-block-error")
        self._render_collapsed()

    def _render_collapsed(self) -> None:
        """渲染collapsed。"""
        if self._is_error:
            self.update(f"  ✗ {self._title} ({self._elapsed:.1f}s)")
        else:
            self.update(f"  ✓ {self._title} ({self._elapsed:.1f}s)")

    def _render_expanded(self) -> None:
        """渲染expanded。"""
        if self._is_error:
            header = f"  ✗ {self._title} ({self._elapsed:.1f}s)"
        else:
            header = f"  ✓ {self._title} ({self._elapsed:.1f}s)"
        detail = _format_detail(self.tool_name, self._arguments, self._full_output)
        self.update(f"{header}\n{detail}")

    def on_click(self) -> None:
        """处理click事件。"""
        if self._loading:
            return
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._render_collapsed()
        else:
            self._render_expanded()


_MODE_CYCLE = [
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
]

_MODE_COLORS = {
    PermissionMode.DEFAULT: "dim",
    PermissionMode.ACCEPT_EDITS: "green",
    PermissionMode.PLAN: "yellow",
    PermissionMode.BYPASS: "red",
}

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _to_past_tense(verb: str) -> str:
    """处理to past tense。"""
    if verb.endswith("ing"):
        stem = verb[:-3]
        if stem.endswith("e"):
            return stem + "d"
        if stem and stem[-1] in "atutitet":
            return stem + "ed"
        return stem + "ed"
    return verb + "ed"


THINKING_VERBS = [
    "Accomplishing", "Architecting", "Baking", "Beboppin'", "Befuddling",
    "Bloviating", "Boogieing", "Boondoggling", "Bootstrapping", "Brewing",
    "Calculating", "Canoodling", "Caramelizing", "Cascading", "Cerebrating",
    "Choreographing", "Churning", "Coalescing", "Cogitating", "Combobulating",
    "Composing", "Computing", "Concocting", "Considering", "Contemplating",
    "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing",
    "Cultivating", "Deciphering", "Deliberating", "Dilly-dallying",
    "Discombobulating", "Doodling", "Elucidating", "Enchanting", "Envisioning",
    "Fermenting", "Finagling", "Flambéing", "Flibbertigibbeting", "Flummoxing",
    "Forging", "Frolicking", "Gallivanting", "Garnishing", "Generating",
    "Germinating", "Grooving", "Harmonizing", "Hatching", "Honking",
    "Hullaballooing", "Ideating", "Imagining", "Improvising", "Incubating",
    "Inferring", "Infusing", "Kneading", "Lollygagging", "Manifesting",
    "Marinating", "Meandering", "Metamorphosing", "Mewing", "Moonwalking",
    "Moseying", "Mulling", "Musing", "Noodling", "Orbiting",
    "Orchestrating", "Percolating", "Philosophising", "Pondering",
    "Pontificating", "Pouncing", "Purring", "Puzzling", "Razzle-dazzling",
    "Ruminating", "Scampering", "Simmering", "Sketching", "Spelunking",
    "Spinning", "Sprouting", "Synthesizing", "Thinking", "Tinkering",
    "Transfiguring", "Transmuting", "Undulating", "Unfurling", "Unravelling",
    "Vibing", "Wandering", "Whisking", "Working", "Wrangling", "Zigzagging",
]  # 与 Go 版 internal/tui/verbs.go 的思考动词列表保持一致。


class ToolGroupSummary(Static, can_focus=True):


    """封装工具 Group Summary相关状态和行为。"""
    def __init__(self, count: int, total_elapsed: float, **kwargs: Any) -> None:
        """初始化工具 Group Summary实例。"""
        label = f"● Done ({count} tool uses · {total_elapsed:.1f}s)  (ctrl+o to expand)"
        super().__init__(label, **kwargs)
        self._count = count
        self._total = total_elapsed
        self._expanded = False

    def _refresh_display(self) -> None:
        """刷新display。"""
        if self._expanded:
            self.update(f"▼ Done ({self._count} tool uses · {self._total:.1f}s)")
        else:
            self.update(
                f"● Done ({self._count} tool uses · {self._total:.1f}s)"
                "  (ctrl+o to expand)"
            )

    def toggle(self) -> None:
        """切换toggle。"""
        self._expanded = not self._expanded
        self._refresh_display()


    def on_click(self) -> None:
        """处理click事件。"""
        self.toggle()


class SubAgentBlock(Static, can_focus=True):

    """封装Sub Agent Block相关状态和行为。"""
    def __init__(self, agent_type: str, description: str, **kwargs: Any) -> None:
        """初始化Sub Agent Block实例。"""
        super().__init__(**kwargs)
        self._agent_type = agent_type or "agent"
        self._description = description[:60] if description else ""
        self._done = False
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._result_preview = ""
        self._tool_count = 0
        self._render_running()

    def _render_running(self) -> None:
        """渲染running。"""
        desc = f"({self._description})" if self._description else ""
        self.update(f"● {self._agent_type}{desc}\n     Running…")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        """设置结果。"""
        self._done = True
        self._is_error = is_error
        self._elapsed = elapsed
        self._result_preview = output[:300] if output else ""
        self._parse_stats(output)
        self._render_done()

    def _parse_stats(self, output: str) -> None:
        """解析stats。"""
        import re
        m = re.search(r"(\d+)\s+tool", output[:200])
        if m:
            self._tool_count = int(m.group(1))

    def _render_done(self) -> None:
        """渲染done。"""
        desc = f"({self._description})" if self._description else ""
        tool_info = f"{self._tool_count} tool uses · " if self._tool_count else ""
        if self._collapsed:
            self.update(
                f"● {self._agent_type}{desc}\n"
                f"    ⎿  Done ({tool_info}{self._elapsed:.1f}s)  (ctrl+o to expand)"
            )
        else:
            self.update(
                f"● {self._agent_type}{desc}\n"
                f"    ⎿  Done ({tool_info}{self._elapsed:.1f}s)\n"
                f"  {self._result_preview}"
            )

    def on_click(self) -> None:
        """处理click事件。"""
        if not self._done:
            return
        self._collapsed = not self._collapsed
        self._render_done()


_LAZYCODE_THEME = Theme(
    name="lazycode",
    primary="#875FFF",
    background="#1a1a1a",
    surface="#1a1a1a",
    panel="#1a1a1a",
    dark=True,
)


class LazyCodeApp(App):
    """实现Mew Code应用。"""
    CSS_PATH = "styles.tcss"
    TITLE = "LazyCode"
    INLINE_PADDING = 0
    theme = "lazycode"
    BINDINGS = [
        Binding("ctrl+c", "handle_ctrl_c", "Quit", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("ctrl+o", "toggle_tool_blocks", "Toggle tools", priority=True),
    ]


    def __init__(
        self,
        providers: list[ProviderConfig],
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
        mcp_servers: list[MCPServerConfig] | None = None,
        hook_engine: HookEngine | None = None,
        enable_fork: bool = False,
        enable_verification_agent: bool = False,
        worktree_config: Any = None,
        teammate_mode: str = "",
        enable_coordinator_mode: bool = False,
    ) -> None:
        """初始化Mew Code 应用实例。"""
        super().__init__()
        self.providers = providers
        self._initial_permission_mode = permission_mode
        self._mcp_server_configs = mcp_servers or []
        self.hook_engine = hook_engine
        self._enable_fork = enable_fork
        self._enable_verification_agent = enable_verification_agent
        self._worktree_config = worktree_config
        self._teammate_mode = teammate_mode
        self._enable_coordinator_mode = enable_coordinator_mode
        self.file_cache = FileCache()
        self.client: LLMClient | None = None
        self.conversation = ConversationManager()
        self.registry: ToolRegistry = create_default_registry(file_cache=self.file_cache)
        self.agent: Agent | None = None
        self.mcp_manager: MCPManager | None = None
        self._mcp_init_task: asyncio.Task[None] | None = None
        self._selected_provider: ProviderConfig | None = None
        self._streaming = False
        self._thinking = False
        self._thinking_start: float = 0.0
        self._thinking_label: Static | None = None
        self._thinking_verb: str = ""
        self._spinner_idx: int = 0
        self._spinner_timer = None
        self._agent_task: asyncio.Task[None] | None = None
        self._subagent_task: asyncio.Task[None] | None = None
        self._subagent_start_time: float | None = None
        self.session_manager: SessionManager | None = None
        self.session: Session | None = None
        self.memory_manager: MemoryManager | None = None
        self._instructions_content: str = ""
        self.command_registry = CommandRegistry()
        register_all_commands(self.command_registry)
        self.skill_loader: SkillLoader | None = None
        self.skill_executor: SkillExecutor | None = None
        self._load_skill_tool: LoadSkill | None = None
        self.agent_loader: AgentLoader | None = None
        self.task_manager: TaskManager = TaskManager()
        self.trace_manager: TraceManager = TraceManager()
        self._notification_check_task: asyncio.Task[None] | None = None
        self.worktree_manager: WorktreeManager | None = None
        self._stale_cleanup_task: asyncio.Task[None] | None = None
        self._current_streaming_label: Static | None = None
        self._current_ai_row: Vertical | None = None
        self._current_accumulated_text: str = ""
        self._mcp_instructions: str = ""
        self._mcp_instructions_ok: bool = False
        self._mcp_connecting: bool = False

    @staticmethod
    def _make_banner(model: str = "", work_dir: str = "") -> RichText:
        """处理make banner。"""
        t = RichText()
        t.append(" /o.o\\    ", style="bold color(220)")
        t.append("LazyCode v0.1.0\n", style="color(242)")
        t.append(" ( ᴥ )   ", style="bold color(220)")
        t.append(f"{model}\n" if model else "\n", style="color(242)")
        t.append(" /   \\    ", style="bold color(220)")
        t.append(work_dir, style="color(242)")
        return t

    def compose(self) -> ComposeResult:
        """组合compose界面。"""
        yield Static(self._make_banner(), id="title-bar")

        if len(self.providers) > 1:
            with Vertical(id="provider-select"):
                yield Static("Select a Provider", id="select-label")
                yield OptionList(
                    *[
                        Option(f"{p.name}  [{p.model}]", id=p.name)
                        for p in self.providers
                    ],
                    id="provider-list",
                )
        yield VerticalScroll(id="chat-area")
        with Vertical(id="input-area"):
            yield CompletionPopup()
            yield ChatInput(id="chat-input")
            with Horizontal(id="status-bar"):
                yield Static("  default", id="mode-label")
                yield Static("", id="token-label")
                yield Static("", id="model-label")

    def on_mount(self) -> None:
        """处理mount事件。"""
        self.register_theme(_LAZYCODE_THEME)
        self.theme = "lazycode"
        if len(self.providers) == 1:
            self._select_provider(self.providers[0])
        else:
            self.query_one("#chat-area").display = False
            self.query_one("#input-area").display = False

    def _select_provider(self, provider: ProviderConfig) -> None:
        """选择provider。"""
        self._selected_provider = provider
        try:
            self.client = create_client(provider)
        except AuthenticationError as e:
            self._show_error(str(e))
            return

        work_dir = os.getcwd()
        home = Path.home()
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(work_dir),
            rule_engine=RuleEngine(
                user_rules_path=home / ".lazycode" / "permissions.yaml",
                project_rules_path=Path(work_dir) / ".lazycode" / "permissions.yaml",
                local_rules_path=Path(work_dir) / ".lazycode" / "permissions.local.yaml",
            ),
            mode=self._initial_permission_mode,
        )

        self._instructions_content = load_instructions(work_dir)
        self.memory_manager = MemoryManager(work_dir)
        self.session_manager = SessionManager(work_dir)
        self.session_manager.cleanup()
        self.session = self.session_manager.create()

        load_skill_tool = LoadSkill()
        self.registry.register(load_skill_tool)
        self._load_skill_tool = load_skill_tool

        self.registry.register(
            ToolSearchTool(self.registry, protocol=provider.protocol)
        )
        self.registry.register(AskUserTool())

        self.agent = Agent(
            client=self.client,
            registry=self.registry,
            protocol=provider.protocol,
            work_dir=work_dir,
            permission_checker=checker,
            context_window=provider.context_window,
            instructions_content=self._instructions_content,
            memory_manager=self.memory_manager,
            hook_engine=self.hook_engine,
        )

        self.skill_loader = SkillLoader(work_dir)
        self.skill_loader.load_all()

        load_skill_tool.set_loader(self.skill_loader)
        load_skill_tool.set_agent(self.agent)

        self.skill_executor = SkillExecutor(
            agent=self.agent,
            client=self.client,
            protocol=provider.protocol,
        )

        catalog = self.skill_loader.get_catalog()
        if catalog:
            lines = [
                "You can use the following Skills:",
                "",
            ]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "If the user's request matches a Skill, call LoadSkill to activate it."
            )
            self.agent.set_skill_catalog("\n".join(lines))

        register_skill_commands(
            self.command_registry, self.skill_loader, self.skill_executor
        )

        from lazycode.config import WorktreeConfig
        wt_cfg = self._worktree_config or WorktreeConfig()
        self.worktree_manager = WorktreeManager(
            repo_root=work_dir,
            symlink_directories=wt_cfg.symlink_directories,
        )
        restored = self.worktree_manager.restore_session()
        if restored:
            self.agent.work_dir = restored.worktree_path

        wt_command = create_worktree_command(self.worktree_manager)
        self.command_registry.register_sync(wt_command)

        from lazycode.tools.enter_worktree import EnterWorktreeTool
        from lazycode.tools.exit_worktree import ExitWorktreeTool
        self.registry.register(EnterWorktreeTool(worktree_manager=self.worktree_manager))
        self.registry.register(ExitWorktreeTool(worktree_manager=self.worktree_manager))

        self._stale_cleanup_task = asyncio.create_task(
            start_stale_cleanup_task(
                self.worktree_manager,
                wt_cfg.stale_cleanup_interval,
                wt_cfg.stale_cutoff_hours,
            )
        )

        self.agent_loader = AgentLoader(
            work_dir, enable_verification=self._enable_verification_agent
        )
        self.agent_loader.load_all()

        from lazycode.teams.manager import TeamManager
        from lazycode.tools.team_create import TeamCreateTool
        from lazycode.tools.team_delete import TeamDeleteTool

        self.team_manager = TeamManager(worktree_manager=self.worktree_manager, trace_manager=self.trace_manager)

        agent_tool = AgentTool(
            agent_loader=self.agent_loader,
            task_manager=self.task_manager,
            trace_manager=self.trace_manager,
            parent_agent=self.agent,
            enable_fork=self._enable_fork,
            provider_config=provider,
            worktree_manager=self.worktree_manager,
            team_manager=self.team_manager,
        )
        self.registry.register(agent_tool)

        team_create_tool = TeamCreateTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
            teammate_mode=self._teammate_mode,
            is_interactive=True,
            enable_coordinator_mode=self._enable_coordinator_mode,
        )
        self.registry.register(team_create_tool)

        team_delete_tool = TeamDeleteTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
        )
        self.registry.register(team_delete_tool)

        agent_catalog = self.agent_loader.list_agents()
        if agent_catalog:
            lines = [
                "## Available Sub-Agent Types",
                "",
                "Use the Agent tool with subagent_type parameter to delegate tasks:",
                "",
            ]
            for agent_type, when_to_use in agent_catalog:
                lines.append(f"- **{agent_type}**: {when_to_use}")
            if self._enable_fork:
                lines.append("")
                lines.append(
                    "Leave subagent_type empty to fork the current conversation "
                    "(inherits full dialog history)."
                )
            lines.append("")
            lines.append(
                "IMPORTANT: Sub-agents run in the background. "
                "After calling the Agent tool, you will get a task ID immediately. "
                "Do NOT wait, sleep, or poll for the result. "
                "Simply report the task ID to the user and end your turn. "
                "The system will automatically notify when the task completes."
            )
            self.agent.set_agent_catalog("\n".join(lines), catalog_list=agent_catalog)

        tasks_cmd = create_tasks_command(self.task_manager)
        self.command_registry.register_sync(tasks_cmd)

        from lazycode.commands.handlers.trace import create_trace_command
        trace_cmd = create_trace_command(self.trace_manager, self.agent.agent_id)
        self.command_registry.register_sync(trace_cmd)

        from lazycode.tools.synthetic_output import SyntheticOutputTool

        self.registry.register(SyntheticOutputTool())
        self.agent._team_manager = self.team_manager

        if self.hook_engine:
            asyncio.ensure_future(
                self.hook_engine.run_hooks(
                    "startup", HookContext(event_name="startup")
                )
            )

        if self._mcp_server_configs:
            self._mcp_init_task = asyncio.create_task(self._init_mcp())

        self.query_one("#model-label", Static).update(provider.model)
        work_dir = os.getcwd()
        self.query_one("#title-bar", Static).update(
            self._make_banner(provider.model, work_dir)
        )
        self._update_mode_label()

        select = self.query("#provider-select")
        if select:
            select.first().display = False
        self.query_one("#chat-area").display = True
        self.query_one("#input-area").display = True
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.placeholder = "Send a message..."
        chat_input.load_history(work_dir)
        chat_input.focus()

        self._notification_check_task = asyncio.create_task(
            self._start_notification_polling()
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """处理option list option selected事件。"""
        if event.option_list.id == "provider-list":
            provider = self.providers[event.option_index]
            self._select_provider(provider)

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------

    def add_system_message(self, text: str) -> None:
        """处理add system 消息。"""
        self._show_system_message(text)

    def send_user_message(self, text: str) -> None:
        """发送user 消息。"""
        if self._streaming or self.agent is None:
            return
        self._agent_task = asyncio.create_task(self._send_message(text))

    def set_plan_mode(self, enabled: bool) -> None:
        """设置plan mode。"""
        if self.agent is None:
            return
        mode = PermissionMode.PLAN if enabled else PermissionMode.DEFAULT
        self.agent.set_permission_mode(mode)
        self._update_mode_label()

    def get_token_count(self) -> tuple[int, int]:
        """获取token count。"""
        if self.agent:
            return self.agent.total_input_tokens, self.agent.total_output_tokens
        return 0, 0

    def refresh_status(self) -> None:
        """刷新状态。"""
        self._update_mode_label()

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------


    def _build_command_context(self, args: str) -> CommandContext:
        """构建命令 上下文。"""
        return CommandContext(
            args=args,
            agent=self.agent,
            conversation=self.conversation,
            session=self.session,
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            ui=self,
            config={
                "registry": self.command_registry,
                "set_session": self._set_session,
                "set_conversation": self._set_conversation,
                "clear_chat": self._clear_chat,
                "render_restored": self._render_restored_messages,
                "skill_loader": self.skill_loader,
                "skill_executor": self.skill_executor,
            },
        )

    def _set_session(self, session: Session) -> None:
        """设置会话。"""
        self.session = session

    def _set_conversation(self, conv: ConversationManager) -> None:
        """设置对话。"""
        self.conversation = conv

    def _clear_chat(self) -> None:
        """清理chat。"""
        chat = self.query_one("#chat-area", VerticalScroll)
        chat.remove_children()

    async def _dispatch_command(self, text: str) -> None:
        """异步处理dispatch 命令。"""
        name, args, is_command = parse_command(text)

        if not is_command:
            if self._streaming or self.agent is None:
                return
            self._agent_task = asyncio.create_task(self._send_message(text))
            return

        if name == "":
            commands = self.command_registry.list_commands()
            lines = ["可用命令："]
            for cmd in commands:
                aliases_str = ", ".join(f"/{a}" for a in cmd.aliases)
                name_part = f"/{cmd.name}"
                if aliases_str:
                    name_part += f", {aliases_str}"
                lines.append(f"  {name_part:<24} {cmd.description}")
            self._show_system_message("\n".join(lines))
            return

        cmd = self.command_registry.find(name)
        if cmd is None:
            self._show_system_message(f"未知命令：/{name}，输入 /help 查看可用命令")
            return

        if not args and cmd.arg_prompt:
            self._show_system_message(cmd.arg_prompt)
            return

        ctx = self._build_command_context(args)
        try:
            await cmd.handler(ctx)
        except Exception as e:
            self._show_error(f"命令执行失败: {e}")

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """处理chat 输入 submitted事件。"""
        text = event.text.strip()
        if self._streaming and not text.startswith("/"):
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
                self._streaming = False
                self._stop_spinner()
                self._show_system_message("(response interrupted)")
                await asyncio.sleep(0.05)
        await self._dispatch_command(text)

    def on_chat_input_tab_complete(self, event: ChatInput.TabComplete) -> None:
        """处理chat 输入 tab complete事件。"""
        matches = complete(self.command_registry, event.text)
        if not matches:
            return
        popup = self.query_one(CompletionPopup)
        if len(matches) == 1:
            input_widget = self.query_one("#chat-input", ChatInput)
            input_widget.clear()
            input_widget.insert(matches[0] + " ")
        else:
            popup.show(matches)

    def on_chat_input_completion_request(self, event: ChatInput.CompletionRequest) -> None:
        """处理输入框补全请求。"""
        popup = self.query_one(CompletionPopup)
        if event.kind == "command":
            matches = complete(self.command_registry, event.prefix)
        elif event.kind == "file":
            work_dir = self.agent.work_dir if self.agent else os.getcwd()
            matches = [f"@{m}" for m in scan_files_for_at(event.prefix, work_dir)]
        else:
            matches = []

        if matches:
            popup.show(matches)
        else:
            popup.hide()

    def on_completion_popup_selected(self, event: CompletionPopup.Selected) -> None:
        """处理completion popup selected事件。"""
        input_widget = self.query_one("#chat-input", ChatInput)
        input_area = self.query("#input-area")
        if input_area:
            input_area.first().display = True
        selected = event.value
        text = input_widget.text
        if selected.startswith("@"):
            at_idx = text.rfind("@")
            if at_idx >= 0:
                suffix = "" if selected.endswith("/") else " "
                input_widget.clear()
                input_widget.insert(text[:at_idx] + selected + suffix)
                self.set_focus(input_widget)
                return
        input_widget.clear()
        input_widget.insert(selected + " ")
        self.set_focus(input_widget)

    def action_cycle_mode(self) -> None:
        """执行cycle mode动作。"""
        if self.agent is None:
            return
        current = self.agent.permission_mode
        try:
            idx = _MODE_CYCLE.index(current)
        except ValueError:
            idx = 0
        next_mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
        self.agent.set_permission_mode(next_mode)
        self._update_mode_label()

    def action_toggle_tool_blocks(self) -> None:
        """执行toggle 工具 blocks动作。"""
        for block in self.query(ToolCallBlock):
            if block._loading:
                continue
            block._collapsed = not block._collapsed
            if block._collapsed:
                block._render_collapsed()
            else:
                block._render_expanded()

        for summary in self.query(ToolGroupSummary):
            was_expanded = summary._expanded
            summary.toggle()
            parent = summary.parent
            if parent:
                for child in parent.children:
                    if isinstance(child, ToolCallBlock) and child.tool_name in COLLAPSIBLE_TOOLS:
                        child.display = summary._expanded

        for block in self.query(SubAgentBlock):
            if block._done:
                block._collapsed = not block._collapsed
                block._render_done()

    def action_cancel(self) -> None:
        """执行cancel动作。"""
        popup = self.query_one(CompletionPopup)
        if popup.is_visible:
            popup.hide()
            self.query_one("#chat-input", ChatInput).focus()
            return
        if self._agent_task and not self._agent_task.done():
            if self._subagent_task and not self._subagent_task.done():
                task_id = self.task_manager.adopt_running(
                    self._subagent_task, "background task"
                ) if hasattr(self.task_manager, 'adopt_running') else None
                if task_id:
                    self._show_system_message(
                        f"Task moved to background (id: {task_id})"
                    )
                    return
            self._agent_task.cancel()

    async def _send_message(self, text: str, is_notification: bool = False) -> None:
        """发送消息。"""
        assert self.agent is not None

        if self._mcp_init_task and not self._mcp_init_task.done():
            self._show_system_message("Waiting for MCP servers to connect...")
            await self._mcp_init_task

        self._streaming = True
        chat = self.query_one("#chat-area", VerticalScroll)
        input_widget = self.query_one("#chat-input", ChatInput)

        if text and "@" in text:
            text = expand_at_refs(text, self.agent.work_dir)

        if text:
            user_row = Vertical(classes="user-row")
            await chat.mount(user_row)
            from rich.text import Text as RichText
            user_rich = RichText()
            user_rich.append("❯ ", style="bold color(80)")
            user_rich.append(text, style="bold color(255)")
            user_bubble = Static(user_rich, classes="message user-message")
            await user_row.mount(user_bubble)
            chat.scroll_end(animate=False)

            self.conversation.add_user_message(text)
            if self.session:
                self.session.append(Message(role="user", content=text))

        if self._mcp_instructions and not self._mcp_instructions_ok:
            self.conversation.add_system_reminder(self._mcp_instructions)
            self._mcp_instructions_ok = True

        history_cursor = len(self.conversation.history)

        ai_row = Vertical(classes="ai-row")
        await chat.mount(ai_row)
        streaming_label = Static("", classes="message ai-message")
        await ai_row.mount(streaming_label)

        accumulated_text = ""
        tool_blocks: dict[str, ToolCallBlock] = {}

        try:
            async for event in self.agent.run(self.conversation):
                if isinstance(event, ThinkingText):
                    if not self._thinking:
                        self._thinking = True
                        self._thinking_start = _time.monotonic()
                        self._thinking_verb = random.choice(THINKING_VERBS)
                        self._spinner_idx = 0
                        if streaming_label is not None:
                            frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
                            streaming_label.update(f"  {frame} {self._thinking_verb}…")
                            self._thinking_label = streaming_label
                            self._start_spinner()
                    chat.scroll_end(animate=False)

                elif isinstance(event, StreamText):
                    if self._thinking and self._thinking_label is not None:
                        self._stop_spinner()
                        elapsed = _time.monotonic() - self._thinking_start
                        past = _to_past_tense(self._thinking_verb)
                        self._thinking_label.update(
                            f"✻ {past} for {elapsed:.1f}s"
                        )
                        self._thinking_label.add_class("thinking-done")
                        self._thinking = False
                        self._thinking_label = None
                        streaming_label = Static("", classes="message ai-message")
                        await ai_row.mount(streaming_label)
                    accumulated_text += event.text
                    from rich.text import Text as RichText
                    t = RichText()
                    t.append("● ", style="bold color(99)")
                    t.append(accumulated_text)
                    streaming_label.update(t)
                    chat.scroll_end(animate=False)

                elif isinstance(event, RetryEvent):
                    self._show_system_message(f"↻ Retrying: {event.reason}")

                elif isinstance(event, ToolUseEvent):
                    if accumulated_text:
                        await streaming_label.remove()
                        from rich.text import Text as RichText
                        prefix = Static(RichText("●  ", style="bold color(99)"), classes="message")
                        await ai_row.mount(prefix)
                        md = Markdown(accumulated_text, classes="message ai-message")
                        await ai_row.mount(md)
                        streaming_label = None
                        accumulated_text = ""
                    elif streaming_label is not None:
                        await streaming_label.remove()
                        streaming_label = None

                    if _is_subagent_tool(event.tool_name):
                        agent_type = event.arguments.get("subagent_type", "")
                        desc = event.arguments.get("description", "")
                        block = SubAgentBlock(
                            agent_type or "agent",
                            desc,
                            classes="tool-block subagent-block",
                        )
                    else:
                        block = ToolCallBlock(
                            event.tool_name, event.arguments, classes="tool-block"
                        )
                    await ai_row.mount(block)
                    tool_blocks[event.tool_id] = block
                    chat.scroll_end(animate=False)

                elif isinstance(event, PermissionRequest):
                    await self._handle_permission_request(event)

                elif isinstance(event, ToolResultEvent):
                    block = tool_blocks.get(event.tool_id)
                    if block:
                        block.set_result(event.output, event.is_error, event.elapsed)
                    chat.scroll_end(animate=False)

                    ask_tool = self.registry.get("AskUserQuestion")
                    if ask_tool and isinstance(ask_tool, AskUserTool) and ask_tool._pending_event:
                        await self._handle_askuser(ask_tool._pending_event)

                elif isinstance(event, TurnComplete):
                    if self.session:
                        for msg in self.conversation.history[history_cursor:]:
                            self.session.append(msg)
                        history_cursor = len(self.conversation.history)

                    collapsible = [
                        (tid, blk) for tid, blk in tool_blocks.items()
                        if isinstance(blk, ToolCallBlock)
                        and blk.tool_name in COLLAPSIBLE_TOOLS
                        and not blk._loading
                    ]
                    if len(collapsible) >= 2:
                        total_elapsed = sum(b._elapsed for _, b in collapsible)
                        summary = ToolGroupSummary(
                            len(collapsible), total_elapsed,
                            classes="tool-block tool-group-summary",
                        )
                        for _, blk in collapsible:
                            blk.display = False
                        await ai_row.mount(summary)

                    tool_blocks.clear()
                    ai_row = Vertical(classes="ai-row")
                    await chat.mount(ai_row)
                    streaming_label = Static("", classes="message ai-message")
                    await ai_row.mount(streaming_label)
                    accumulated_text = ""
                    chat.scroll_end(animate=False)

                elif isinstance(event, UsageEvent):
                    self._update_token_label(event.input_tokens, event.output_tokens)

                elif isinstance(event, HookEvent):
                    status = "✓" if event.success else "✗"
                    self._show_system_message(
                        f"Hook [{event.hook_id}] {status} {event.output}"
                    )

                elif isinstance(event, CompactNotification):
                    self._show_system_message(event.message)

                elif isinstance(event, ErrorEvent):
                    self._show_error(event.message)

                elif isinstance(event, LoopComplete):
                    total_time = _time.monotonic() - self._thinking_start
                    if self._thinking and self._thinking_label is not None:
                        self._stop_spinner()
                        past = _to_past_tense(self._thinking_verb)
                        self._thinking_label.update(
                            f"✻ {past} for {total_time:.1f}s"
                        )
                        self._thinking_label.add_class("thinking-done")
                        self._thinking = False
                        self._thinking_label = None
                    else:
                        done_label = Static(
                            f"✻ {_to_past_tense(self._thinking_verb)} for {total_time:.1f}s",
                            classes="message thinking-done",
                        )
                        await ai_row.mount(done_label)
                    if self.session:
                        for msg in self.conversation.history[history_cursor:]:
                            self.session.append(msg)
                        history_cursor = len(self.conversation.history)
                        self.session.meta.total_tokens = (
                            self.agent.total_input_tokens
                            + self.agent.total_output_tokens
                        )
                        asyncio.ensure_future(
                            self._update_session_summary()
                        )
                    if self.agent.plan_mode:
                        asyncio.ensure_future(
                            self._show_plan_approval()
                        )

            if accumulated_text and streaming_label is not None:
                await streaming_label.remove()
                md = Markdown(accumulated_text, classes="message ai-message")
                await ai_row.mount(md)
            elif streaming_label is not None:
                await streaming_label.remove()

            chat.scroll_end(animate=False)

        except asyncio.CancelledError:
            if accumulated_text:
                if streaming_label is not None:
                    await streaming_label.remove()
                md = Markdown(
                    accumulated_text + "\n\n*[cancelled]*",
                    classes="message ai-message",
                )
                await ai_row.mount(md)
            self._show_system_message("Operation cancelled")
        except LLMError as e:
            self._show_error(str(e))
        finally:
            self._streaming = False
            self._agent_task = None
            self._stop_spinner()
            input_widget.focus()

            await self._process_task_notifications()

    async def _process_task_notifications(self) -> None:
        """异步处理process 任务 通知。"""
        completed = self.task_manager.poll_completed()
        if not completed or self.agent is None:
            return

        inject_task_notifications(self.conversation, completed)

        for task in completed:
            status_icon = "✓" if task.status == "completed" else "✗"
            self._show_system_message(
                f"{status_icon} 后台任务完成: [{task.id}] {task.name} — {task.status}"
            )

            if hasattr(self, 'team_manager'):
                self.team_manager.on_teammate_completed(task.agent.agent_id)

        self._agent_task = asyncio.create_task(
            self._send_message("", is_notification=True)
        )

    async def _start_notification_polling(self) -> None:
        """启动通知 polling。"""
        while True:
            await asyncio.sleep(3)
            if not self._streaming and self.agent is not None:
                await self._process_task_notifications()

    async def _show_plan_approval(self) -> None:
        """异步处理show plan approval。"""
        from lazycode.plan_dialog import InlinePlanWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlinePlanWidget()
        await chat.mount(widget)
        chat.scroll_end(animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_plan_widget_responded(
        self, event: "InlinePlanWidget.Responded"
    ) -> None:
        """处理inline plan widget responded事件。"""
        from lazycode.plan_dialog import InlinePlanWidget, PlanChoice

        try:
            self.query_one("#plan-inline", InlinePlanWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

        if self.agent is None:
            return

        choice = event.choice
        feedback = event.feedback
        plan_path = self.agent._get_plan_path()
        plan_content = ""
        if plan_path.exists():
            try:
                plan_content = plan_path.read_text(encoding="utf-8")
            except Exception:
                pass

        if choice == PlanChoice.YOLO:
            self.agent.set_permission_mode(PermissionMode.BYPASS)
            self._update_mode_label()
            if plan_content:
                self.send_user_message(f"Execute this plan:\n\n{plan_content}")
        elif choice == PlanChoice.MANUAL:
            self.agent.set_permission_mode(PermissionMode.DEFAULT)
            self._update_mode_label()
            if plan_content:
                self.send_user_message(f"Execute this plan:\n\n{plan_content}")
        elif choice == PlanChoice.FEEDBACK:
            if feedback:
                self.send_user_message(feedback)
            else:
                self._show_system_message("Type your feedback and send.")

    async def _handle_askuser(self, event: AskUserEvent) -> None:
        """处理askuser。"""
        from lazycode.askuser_dialog import InlineAskUserWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlineAskUserWidget(event.questions)
        self._pending_askuser_event = event
        await chat.mount(widget)
        chat.scroll_end(animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_ask_user_widget_responded(
        self, event: "InlineAskUserWidget.Responded"
    ) -> None:
        """处理inline ask user widget responded事件。"""
        from lazycode.askuser_dialog import InlineAskUserWidget

        req = getattr(self, "_pending_askuser_event", None)
        if req is not None and not req.future.done():
            req.future.set_result(event.answers if event.answers else {})
            self._pending_askuser_event = None
        try:
            self.query_one("#askuser-inline", InlineAskUserWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    def _start_spinner(self) -> None:
        """启动spinner。"""
        if self._spinner_timer is not None:
            return
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _stop_spinner(self) -> None:
        """停止spinner。"""
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _tick_spinner(self) -> None:
        """处理tick spinner。"""
        self._spinner_idx += 1
        if self._thinking and self._thinking_label is not None:
            frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
            elapsed = _time.monotonic() - self._thinking_start
            self._thinking_label.update(
                f"  {frame} {self._thinking_verb}…  ({elapsed:.0f}s)"
            )

    async def _handle_permission_request(self, request: PermissionRequest) -> None:
        """处理权限 请求。"""
        from lazycode.permission_dialog import InlinePermissionWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlinePermissionWidget(request.tool_name, request.description)
        self._pending_perm_request = request
        await chat.mount(widget)
        chat.scroll_end(animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_permission_widget_responded(
        self, event: "InlinePermissionWidget.Responded"
    ) -> None:
        """处理inline 权限 widget responded事件。"""
        from lazycode.permission_dialog import InlinePermissionWidget

        req = getattr(self, "_pending_perm_request", None)
        if req is not None:
            req.future.set_result(event.response)
            self._pending_perm_request = None
        try:
            widget = self.query_one("#perm-inline", InlinePermissionWidget)
            widget.remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------

    async def _render_restored_messages(self, messages: list[Message]) -> None:
        """渲染restored 消息。"""
        chat = self.query_one("#chat-area", VerticalScroll)
        await chat.remove_children()

        for msg in messages:
            if msg.tool_results or not msg.content:
                continue
            if msg.role == "user":
                row = Vertical(classes="user-row")
                await chat.mount(row)
                user_rich = RichText()
                user_rich.append("❯ ", style="bold color(80)")
                user_rich.append(msg.content, style="bold color(255)")
                bubble = Static(user_rich, classes="message user-message")
                await row.mount(bubble)
            elif msg.role == "assistant":
                row = Vertical(classes="ai-row")
                await chat.mount(row)
                md = Markdown(msg.content, classes="message ai-message")
                await row.mount(md)

        chat.scroll_end(animate=False)

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------

    async def _update_session_summary(self) -> None:
        """更新会话 summary。"""
        if not self.session or not self.client or not self.agent:
            return
        try:
            summary = await generate_session_summary(
                self.client, self.conversation, self.agent.protocol
            )
            if summary:
                self.session.meta.summary = summary
                self.session.meta.save(
                    self.session._sessions_dir / f"{self.session.session_id}.meta"
                )
        except Exception:
            pass

    # -----------------------------------------------------------------
    # MCP
    # -----------------------------------------------------------------

    async def _init_mcp(self) -> None:
        """异步处理init MCP。"""
        self._mcp_connecting = True
        self._update_mode_label()
        manager = MCPManager()
        manager.load_configs(self._mcp_server_configs)
        tools_before = len(self.registry.list_tools())
        errors = await manager.register_all_tools(self.registry)
        self.mcp_manager = manager
        self._mcp_connecting = False
        self._update_mode_label()
        for err in errors:
            self._show_system_message(f"MCP warning: {err}")
        tools_after = len(self.registry.list_tools())
        mcp_tools = tools_after - tools_before
        server_count = len(manager._clients)
        if server_count > 0:
            self._show_system_message(
                f"Connected to {server_count} MCP server(s), {mcp_tools} tools registered"
            )
        if server_count > 0 and mcp_tools > 0:
            parts = []
            for cfg in self._mcp_server_configs:
                srv_name = cfg.name if hasattr(cfg, 'name') else str(cfg)
                tool_names = [
                    t.name for t in self.registry.list_tools()
                    if getattr(t, "is_mcp_tool", False)
                    and getattr(t, "mcp_server_name", "") == srv_name
                ]
                section = f"## {srv_name}\n"
                if tool_names:
                    section += "Available tools: " + ", ".join(tool_names)
                parts.append(section)
            self._mcp_instructions = (
                "# MCP Server Instructions\n\n"
                "The following MCP servers are connected. "
                "Use their tools when the user asks.\n\n"
                + "\n\n".join(parts)
            )

    async def _shutdown_mcp(self) -> None:
        """异步处理shutdown MCP。"""
        if self._mcp_init_task is not None:
            self._mcp_init_task.cancel()
            try:
                await self._mcp_init_task
            except (asyncio.CancelledError, Exception):
                pass
            self._mcp_init_task = None
        if self.mcp_manager is not None:
            await self.mcp_manager.shutdown()
            self.mcp_manager = None

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------

    async def action_handle_ctrl_c(self) -> None:
        """执行handle ctrl c动作。"""
        if self._streaming:
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
            self._show_system_message("(response interrupted)")
            self._streaming = False
            self._stop_spinner()
            if self._thinking and self._thinking_label is not None:
                elapsed = _time.monotonic() - self._thinking_start
                past = _to_past_tense(self._thinking_verb)
                self._thinking_label.update(f"✻ {past} for {elapsed:.1f}s")
                self._thinking_label.add_class("thinking-done")
                self._thinking = False
                self._thinking_label = None
            try:
                inp = self.query_one("#chat-input", ChatInput)
                inp.disabled = False
                inp.focus()
            except Exception:
                pass
            return

        if self.agent and self.agent.memory_manager:
            try:
                await asyncio.wait_for(
                    self.agent._extract_memories(self.conversation),
                    timeout=10.0,
                )
            except (asyncio.TimeoutError, Exception):
                pass

        if self.hook_engine:
            try:
                await self.hook_engine.run_hooks(
                    "shutdown", HookContext(event_name="shutdown")
                )
            except Exception:
                pass

        if self._stale_cleanup_task and not self._stale_cleanup_task.done():
            self._stale_cleanup_task.cancel()

        if hasattr(self, 'team_manager'):
            for name in list(self.team_manager._teams):
                try:
                    team = self.team_manager._teams[name]
                    for m in team.members:
                        team.set_member_active(m.name, False)
                    self.team_manager.delete_team(name)
                except Exception:
                    pass

        if self.worktree_manager:
            for wt in list(self.worktree_manager.active.values()):
                try:
                    await self.worktree_manager._remove_worktree(wt.name, wt)
                except Exception:
                    pass

        if self.session:
            self.session.close()

        await self._shutdown_mcp()
        self.exit()

    def _show_error(self, text: str) -> None:
        """处理show error。"""
        chat = self.query_one("#chat-area", VerticalScroll)
        error_widget = Static(f"✖ {text}", classes="message error-message")
        chat.mount(error_widget)
        chat.scroll_end(animate=False)

    def _show_system_message(self, text: str) -> None:
        """处理show system 消息。"""
        chat = self.query_one("#chat-area", VerticalScroll)
        msg = Static(f"  {text}", classes="message system-message")
        chat.mount(msg)
        chat.scroll_end(animate=False)

    _MODE_DISPLAY = {
        PermissionMode.DEFAULT: "default",
        PermissionMode.ACCEPT_EDITS: "accept-edits",
        PermissionMode.PLAN: "plan",
        PermissionMode.BYPASS: "YOLO",
    }

    def _update_mode_label(self) -> None:
        """更新mode label。"""
        if self.agent:
            perm = self.agent.permission_mode
            display = self._MODE_DISPLAY.get(perm, perm.value)
            color = _MODE_COLORS.get(perm, "dim")
            label = self.query_one("#mode-label", Static)
            if perm == PermissionMode.DEFAULT:
                label.update(f"[{color}]{display}[/{color}]")
            else:
                label.update(f"[{color}]{display}[/{color}]  (shift+tab to cycle)")
        try:
            model_label = self.query_one("#model-label", Static)
            model_text = self._selected_provider.model if self._selected_provider else ""
            if self._mcp_connecting:
                model_label.update(f"[yellow]MCP connecting…[/yellow]  {model_text}")
            else:
                model_label.update(model_text)
        except Exception:
            pass

    def _update_token_label(self, input_tokens: int, output_tokens: int) -> None:
        """更新token label。"""
        label = self.query_one("#token-label", Static)
        label.update(f"Token: {input_tokens:,} in / {output_tokens:,} out")
