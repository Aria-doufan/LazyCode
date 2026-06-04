from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import TextArea

from lazycode.agent import UsageEvent
from lazycode.app import ChatInput, LazyCodeApp
from lazycode.commands.completion import CompletionPopup


class CompletionPopupTestApp(App):
    def compose(self) -> ComposeResult:
        yield TextArea(id="input")
        yield CompletionPopup()


class UsageEventAgent:
    work_dir = "."

    async def run(self, _conversation):
        yield UsageEvent(1_234, 56)


class ChatInputCompletionTestApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, str]] = []
        self.tab_requests: list[str] = []
        self.selected: list[str] = []

    def compose(self) -> ComposeResult:
        yield ChatInput(id="chat-input")
        yield CompletionPopup()

    def on_chat_input_completion_request(self, event: ChatInput.CompletionRequest) -> None:
        self.requests.append((event.kind, event.prefix))

    def on_chat_input_tab_complete(self, event: ChatInput.TabComplete) -> None:
        self.tab_requests.append(event.text)

    def on_completion_popup_selected(self, event: CompletionPopup.Selected) -> None:
        self.selected.append(event.value)
        self.query_one("#chat-input", ChatInput).focus()


def test_chat_input_detects_slash_completion_context() -> None:
    assert ChatInput._completion_context("/") == ("command", "/")
    assert ChatInput._completion_context("/he") == ("command", "/he")
    assert ChatInput._completion_context("/help") == ("command", "/help")


def test_chat_input_stops_slash_completion_after_arguments() -> None:
    assert ChatInput._completion_context("/help ") is None
    assert ChatInput._completion_context("/session resume") is None
    assert ChatInput._completion_context("hello /help") is None


def test_chat_input_detects_at_file_completion_context() -> None:
    assert ChatInput._completion_context("@") == ("file", "")
    assert ChatInput._completion_context("@app") == ("file", "app")
    assert ChatInput._completion_context("please read @lazycode/app") == ("file", "lazycode/app")


def test_chat_input_stops_at_completion_after_separator() -> None:
    assert ChatInput._completion_context("please read @lazycode/app ") is None
    assert ChatInput._completion_context("please read @lazycode/app\n") is None
    assert ChatInput._completion_context("no completion here") is None


@pytest.mark.asyncio
async def test_chat_input_emits_slash_completion_request() -> None:
    app = ChatInputCompletionTestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        input_widget.insert("/he")
        await pilot.pause()

        assert app.requests[-1] == ("command", "/he")


@pytest.mark.asyncio
async def test_chat_input_emits_at_completion_request() -> None:
    app = ChatInputCompletionTestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        input_widget.insert("please read @lazy")
        await pilot.pause()

        assert app.requests[-1] == ("file", "lazy")


@pytest.mark.asyncio
async def test_down_key_moves_completion_selection_without_changing_focus() -> None:
    app = ChatInputCompletionTestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        popup = app.query_one(CompletionPopup)

        input_widget.focus()
        popup.show(["@app.py", "@agent.py"])
        await pilot.press("down")

        assert app.focused is input_widget
        assert popup.selected_value() == "@agent.py"


@pytest.mark.asyncio
async def test_up_key_moves_completion_selection_without_changing_focus() -> None:
    app = ChatInputCompletionTestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        popup = app.query_one(CompletionPopup)

        input_widget.focus()
        popup.show(["@app.py", "@agent.py"])
        popup.move_selection(1)
        await pilot.press("up")

        assert app.focused is input_widget
        assert popup.selected_value() == "@app.py"


@pytest.mark.asyncio
async def test_tab_confirms_highlighted_completion_candidate() -> None:
    app = ChatInputCompletionTestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        popup = app.query_one(CompletionPopup)

        input_widget.focus()
        popup.show(["@app.py", "@agent.py"])
        popup.move_selection(1)
        await pilot.press("tab")

        assert app.selected == ["@agent.py"]
        assert popup.is_visible is False
        assert app.focused is input_widget


@pytest.mark.asyncio
async def test_enter_does_not_confirm_visible_completion_candidate() -> None:
    app = ChatInputCompletionTestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        popup = app.query_one(CompletionPopup)

        input_widget.focus()
        input_widget.insert("please read @lazy")
        popup.show(["@lazycode/app.py", "@lazycode/agent.py"])
        popup.move_selection(1)
        await pilot.press("enter")

        assert app.selected == []
        assert "@lazycode/agent.py" not in input_widget.text


@pytest.mark.asyncio
async def test_tab_inserts_literal_tab_for_hidden_popup_leading_space_slash() -> None:
    app = ChatInputCompletionTestApp()
    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        popup = app.query_one(CompletionPopup)

        input_widget.focus()
        input_widget.insert(" /he")
        await pilot.pause()
        assert popup.is_visible is False

        await pilot.press("tab")

        assert input_widget.text == " /he\t"
        assert app.tab_requests == []
        assert app.selected == []


@pytest.mark.asyncio
async def test_selected_at_completion_replaces_current_at_prefix() -> None:
    app = LazyCodeApp(providers=[])
    async with app.run_test():
        input_widget = app.query_one("#chat-input", ChatInput)

        input_widget.insert("please read @lazy")
        app.on_completion_popup_selected(CompletionPopup.Selected("@lazycode/app.py"))

        assert input_widget.text == "please read @lazycode/app.py "
        assert app.focused is input_widget


@pytest.mark.asyncio
async def test_selected_at_directory_completion_keeps_completion_context() -> None:
    app = LazyCodeApp(providers=[])
    async with app.run_test() as pilot:
        app.query_one("#chat-area").display = True
        app.query_one("#input-area").display = True
        input_widget = app.query_one("#chat-input", ChatInput)

        input_widget.insert("please read @doc")
        app.on_completion_popup_selected(CompletionPopup.Selected("@docs/"))
        await pilot.pause()

        assert input_widget.text == "please read @docs/"
        assert ChatInput._completion_context(input_widget.text) == ("file", "docs/")
        assert app.focused is input_widget


@pytest.mark.asyncio
async def test_selected_at_file_completion_finishes_with_space() -> None:
    app = LazyCodeApp(providers=[])
    async with app.run_test():
        input_widget = app.query_one("#chat-input", ChatInput)

        input_widget.insert("please read @docs/module/xx")
        app.on_completion_popup_selected(CompletionPopup.Selected("@docs/module/xxx.md"))

        assert input_widget.text == "please read @docs/module/xxx.md "
        assert ChatInput._completion_context(input_widget.text) is None
        assert app.focused is input_widget


@pytest.mark.asyncio
async def test_selected_command_completion_replaces_input_with_command() -> None:
    app = LazyCodeApp(providers=[])
    async with app.run_test():
        input_widget = app.query_one("#chat-input", ChatInput)

        input_widget.insert("/he")
        app.on_completion_popup_selected(CompletionPopup.Selected("/help"))

        assert input_widget.text == "/help "
        assert app.focused is input_widget


@pytest.mark.asyncio
async def test_app_completion_request_shows_matching_command() -> None:
    app = LazyCodeApp([])
    async with app.run_test():
        popup = app.query_one(CompletionPopup)

        app.on_chat_input_completion_request(ChatInput.CompletionRequest("command", "/he"))

        assert popup.is_visible is True
        assert popup.selected_value() == "/help"


@pytest.mark.asyncio
async def test_app_completion_request_hides_unknown_command() -> None:
    app = LazyCodeApp([])
    async with app.run_test():
        popup = app.query_one(CompletionPopup)
        popup.show(["/help"])

        app.on_chat_input_completion_request(ChatInput.CompletionRequest("command", "/unknown-command"))

        assert popup.is_visible is False


@pytest.mark.asyncio
async def test_app_completion_request_shows_matching_file(tmp_path, monkeypatch) -> None:
    work_dir = tmp_path / "work"
    file_dir = work_dir / "lazycode"
    file_dir.mkdir(parents=True)
    (file_dir / "app.py").write_text("print('ok')", encoding="utf-8")
    monkeypatch.chdir(work_dir)
    app = LazyCodeApp([])

    async with app.run_test():
        popup = app.query_one(CompletionPopup)

        app.on_chat_input_completion_request(ChatInput.CompletionRequest("file", "lazycode/app"))

        assert popup.is_visible is True
        selected = popup.selected_value()
        assert selected is not None
        assert selected.startswith("@lazycode")
        assert selected.endswith("app.py")


@pytest.mark.asyncio
async def test_app_completion_request_hides_traversal_file_prefix(tmp_path, monkeypatch) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    monkeypatch.chdir(work_dir)
    app = LazyCodeApp([])

    async with app.run_test():
        popup = app.query_one(CompletionPopup)
        popup.show(["@safe.py"])

        app.on_chat_input_completion_request(ChatInput.CompletionRequest("file", "../"))

        assert popup.is_visible is False


@pytest.mark.asyncio
async def test_completion_popup_show_does_not_steal_focus() -> None:
    app = CompletionPopupTestApp()
    async with app.run_test():
        input_widget = app.query_one("#input", TextArea)
        popup = app.query_one(CompletionPopup)

        input_widget.focus()
        popup.show(["/clear", "/help"])

        assert popup.is_visible is True
        assert app.focused is input_widget
        assert popup.selected_value() == "/clear"


@pytest.mark.asyncio
async def test_completion_popup_is_inside_input_area_above_chat_input() -> None:
    app = LazyCodeApp(providers=[])
    async with app.run_test():
        input_area = app.query_one("#input-area")
        popup = app.query_one(CompletionPopup)
        input_widget = app.query_one("#chat-input", ChatInput)

        assert popup.parent is input_area
        assert input_widget.parent is input_area
        assert input_area.children.index(popup) < input_area.children.index(input_widget)


@pytest.mark.asyncio
async def test_chat_input_remains_visible_when_completion_has_many_candidates() -> None:
    app = LazyCodeApp(providers=[])
    async with app.run_test(size=(120, 30)) as pilot:
        app.query_one("#chat-area").display = True
        input_area = app.query_one("#input-area")
        input_area.display = True
        popup = app.query_one(CompletionPopup)
        input_widget = app.query_one("#chat-input", ChatInput)

        input_widget.focus()
        popup.show([f"/cmd{i}" for i in range(20)])
        await pilot.pause()

        assert input_area.region.height > 0
        assert popup.region.height > 0
        assert input_widget.region.height > 0
        assert input_widget.region.y + input_widget.region.height <= input_area.region.y + input_area.region.height


@pytest.mark.asyncio
async def test_completion_popup_moves_selected_candidate() -> None:
    app = CompletionPopupTestApp()
    async with app.run_test():
        popup = app.query_one(CompletionPopup)

        popup.show(["/clear", "/help", "/session"])
        popup.move_selection(-1)
        assert popup.selected_value() == "/clear"

        popup.move_selection(1)
        assert popup.selected_value() == "/help"

        popup.move_selection(1)
        assert popup.selected_value() == "/session"

        popup.move_selection(1)
        assert popup.selected_value() == "/session"

        popup.move_selection(-1)
        assert popup.selected_value() == "/help"


@pytest.mark.asyncio
async def test_token_usage_updates_status_bar_label() -> None:
    app = LazyCodeApp(providers=[])
    async with app.run_test():
        app.agent = UsageEventAgent()
        labels = list(app.query("#token-label"))
        assert len(labels) == 1

        await app._send_message("")

        label_text = str(labels[0].content)
        assert "1,234" in label_text
        assert "56" in label_text


def test_banner_uses_yellow_dog_motif() -> None:
    banner = LazyCodeApp._make_banner("test-model", "D:/project")
    plain = banner.plain
    spans = banner.spans

    assert " /o.o\\" in plain
    assert "( ᴥ )" in plain
    assert " /   \\" in plain
    assert "/\\_/\\" not in plain
    assert "( o.o )" not in plain
    assert any("color(220)" in str(span.style) for span in spans)
