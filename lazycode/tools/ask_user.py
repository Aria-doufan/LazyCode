from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from lazycode.tools.base import Tool, ToolResult


class QuestionItem(BaseModel):
    """描述展示给用户的单个结构化问题。"""
    type: str = Field(description="Question type: text, radio, select, checkbox")
    name: str = Field(description="Question identifier")
    message: str = Field(description="Question text to display")
    options: list[str] = Field(
        default_factory=list,
        description="Options for radio/select/checkbox types",
    )


class AskUserParams(BaseModel):
    """保存一次 AskUserQuestion 工具调用中的问题列表。"""
    questions: list[QuestionItem] = Field(
        description="List of questions to ask the user"
    )


class AskUserEvent:


    """把待展示的问题和等待用户回答的 future 绑定在一起。"""
    def __init__(
        self,
        questions: list[dict[str, Any]],
        future: asyncio.Future[dict[str, str]],
    ) -> None:
        """保存问题 payload 和回答 future。"""
        self.questions = questions
        self.future = future


class AskUserTool(Tool):
    """向用户发起结构化提问并等待回答。"""
    name = "AskUserQuestion"
    description = (
        "Ask the user one or more questions when you need information "
        "that cannot be determined from code or context alone. Supports "
        "text input, radio (single select), select, and checkbox (multi select) "
        "question types."
    )
    params_model = AskUserParams
    category: str = "read"
    is_system_tool = True
    should_defer = True


    def __init__(self) -> None:
        """初始化当前待处理的提问事件。"""
        self._pending_event: AskUserEvent | None = None

    async def execute(self, params: AskUserParams) -> ToolResult:
        """创建提问事件、等待回答并格式化结果。"""
        questions_data = [q.model_dump() for q in params.questions]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, str]] = loop.create_future()

        self._pending_event = AskUserEvent(questions=questions_data, future=future)

        try:
            answers = await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            return ToolResult(
                output="User did not respond within 5 minutes", is_error=True
            )
        finally:
            self._pending_event = None

        lines = []
        for q in params.questions:
            answer = answers.get(q.name, "(no answer)")
            lines.append(f"{q.name}: {answer}")

        return ToolResult(output="\n".join(lines))
