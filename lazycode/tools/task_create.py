from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from lazycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from lazycode.teams.manager import TeamManager


class TaskCreateParams(BaseModel):
    """定义新任务的标题、描述、负责人和依赖关系。"""
    title: str
    description: str = ""
    assignee: str = ""
    blocks: list[str] | None = None
    blocked_by: list[str] | None = None


class TaskCreateTool(Tool):
    """在 team 任务板中创建共享任务。"""
    name = "TaskCreate"
    description = (
        "Create a shared task in the team's task board. "
        "Supports dependency tracking with blocks/blocked_by fields."
    )
    params_model = TaskCreateParams
    category = "command"
    is_concurrency_safe = True


    def __init__(self, team_manager: TeamManager, team_name: str, agent_name: str = "") -> None:
        """保存 team 管理器、team 名称和创建者名称。"""
        self._team_manager = team_manager
        self._team_name = team_name
        self._agent_name = agent_name


    async def execute(self, params: BaseModel) -> ToolResult:
        """创建任务并返回任务摘要。"""
        p: TaskCreateParams = params  # type: ignore[assignment]

        store = self._team_manager.get_task_store(self._team_name)
        if store is None:
            return ToolResult(output=f"Task store not found for team '{self._team_name}'", is_error=True)

        task = store.create(
            title=p.title,
            description=p.description,
            assignee=p.assignee,
            blocks=p.blocks,
            blocked_by=p.blocked_by,
            created_by=self._agent_name,
        )

        return ToolResult(
            output=(
                f"Task created:\n"
                f"  ID: {task.id}\n"
                f"  Title: {task.title}\n"
                f"  Status: {task.status}\n"
                f"  Assignee: {task.assignee or '(unassigned)'}"
            )
        )
