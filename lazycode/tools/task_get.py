from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from lazycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from lazycode.teams.manager import TeamManager


class TaskGetParams(BaseModel):
    """定义要查询的任务 ID。"""
    task_id: str


class TaskGetTool(Tool):
    """按 ID 获取共享任务详情。"""
    name = "TaskGet"
    description = "Get details of a shared task by ID, including dependency information."
    params_model = TaskGetParams
    category = "read"
    is_concurrency_safe = True


    def __init__(self, team_manager: TeamManager, team_name: str) -> None:
        """保存 team 管理器和 team 名称。"""
        self._team_manager = team_manager
        self._team_name = team_name


    async def execute(self, params: BaseModel) -> ToolResult:
        """读取任务并格式化状态、负责人和依赖信息。"""
        p: TaskGetParams = params  # type: ignore[assignment]

        store = self._team_manager.get_task_store(self._team_name)
        if store is None:
            return ToolResult(output=f"Task store not found for team '{self._team_name}'", is_error=True)

        task = store.get(p.task_id)
        if task is None:
            return ToolResult(output=f"Task '{p.task_id}' not found", is_error=True)

        lines = [
            f"Task {task.id}:",
            f"  Title:      {task.title}",
            f"  Status:     {task.status}",
            f"  Assignee:   {task.assignee or '(unassigned)'}",
            f"  Created by: {task.created_by or '(unknown)'}",
        ]
        if task.description:
            lines.append(f"  Description: {task.description}")
        if task.blocks:
            lines.append(f"  Blocks:     {', '.join(task.blocks)}")
        if task.blocked_by:
            lines.append(f"  Blocked by: {', '.join(task.blocked_by)}")

        return ToolResult(output="\n".join(lines))
