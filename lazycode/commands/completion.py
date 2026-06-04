from __future__ import annotations

from textual.containers import Vertical
from textual.message import Message as TMessage
from textual.widgets import OptionList
from textual.widgets.option_list import Option


class CompletionPopup(Vertical):

    """封装Completion Popup相关状态和行为。"""
    DEFAULT_CSS = """
    CompletionPopup {
        height: auto;
        max-height: 8;
        display: none;
    }
    CompletionPopup OptionList {
        height: auto;
        max-height: 8;
        background: $surface;
        border: tall $accent;
    }
    """


    class Selected(TMessage):


        """封装Selected相关状态和行为。"""
        def __init__(self, value: str) -> None:
            """初始化Selected实例。"""
            super().__init__()
            self.value = value


    def __init__(self, **kwargs) -> None:
        """初始化CompletionPopup实例。"""
        super().__init__(**kwargs)
        self._items: list[str] = []
        self._selected_index: int | None = None

    def compose(self):
        """组合compose界面。"""
        yield OptionList(id="completion-list")

    def show(self, items: list[str]) -> None:
        """处理show。"""
        ol = self.query_one("#completion-list", OptionList)
        ol.clear_options()
        self._items = list(items)
        self._selected_index = 0 if self._items else None
        for item in self._items:
            ol.add_option(Option(item, id=item))
        ol.highlighted = self._selected_index
        self.display = bool(self._items)

    def hide(self) -> None:
        """处理hide。"""
        self.display = False
        self._selected_index = None
        ol = self.query_one("#completion-list", OptionList)
        ol.highlighted = None

    @property
    def is_visible(self) -> bool:
        """判断visible是否成立。"""
        return self.display

    def move_selection(self, delta: int) -> None:
        """移动当前选中的候选项。"""
        if not self._items:
            self._selected_index = None
            return
        current = self._selected_index if self._selected_index is not None else 0
        self._selected_index = max(0, min(current + delta, len(self._items) - 1))
        ol = self.query_one("#completion-list", OptionList)
        ol.highlighted = self._selected_index

    def selected_value(self) -> str | None:
        """获取当前选中的候选值。"""
        if self._selected_index is None:
            return None
        return self._items[self._selected_index]

    def select_highlighted(self) -> None:
        """选择当前高亮的候选项。"""
        value = self.selected_value()
        if value is None:
            return
        self.post_message(self.Selected(value))
        self.hide()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """处理option list option selected事件。"""
        option_index = getattr(event, "option_index", None)
        if option_index is not None:
            self._selected_index = option_index
        self.select_highlighted()
        event.stop()
