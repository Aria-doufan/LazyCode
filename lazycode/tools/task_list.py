from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from lazycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from lazycode.teams.manager import TeamManager


class TaskListParams(BaseModel):
    """定义任务列表的状态和负责人过滤条件。"""
    status: str | None = None
    assignee: str | None = None


class TaskListTool(Tool):
    """列出 team 任务板中的共享任务。"""
    name = "TaskList"
    description = (
        "List all shared tasks in the team's task board. "
        "Optionally filter by status (pending/in_progress/completed/blocked) or assignee."
    )
    params_model = TaskListParams
    category = "read"
    is_concurrency_safe = True


    def __init__(self, team_manager: TeamManager, team_name: str) -> None:
        """保存 team 管理器和 team 名称。"""
        self._team_manager = team_manager
        self._team_name = team_name


    async def execute(self, params: BaseModel) -> ToolResult:
        """按过滤条件列出任务并格式化紧凑列表。"""
        p: TaskListParams = params  # type: ignore[assignment]

        store = self._team_manager.get_task_store(self._team_name)
        if store is None:
            return ToolResult(output=f"Task store not found for team '{self._team_name}'", is_error=True)

        tasks = store.list_tasks(status=p.status, assignee=p.assignee)

        if not tasks:
            filters = []
            if p.status:
                filters.append(f"status={p.status}")
            if p.assignee:
                filters.append(f"assignee={p.assignee}")
            filter_str = f" (filters: {', '.join(filters)})" if filters else ""
            return ToolResult(output=f"No tasks found{filter_str}")

        status_icons = {
            "pending": "○",
            "in_progress": "◐",
            "completed": "●",
            "blocked": "✕",
        }

        lines = [f"Tasks ({len(tasks)}):"]
        for t in tasks:
            icon = status_icons.get(t.status, "?")
            assignee = f" [{t.assignee}]" if t.assignee else ""
            deps = ""
            if t.blocked_by:
                deps = f" (blocked by: {', '.join(t.blocked_by)})"
            lines.append(f"  {icon} [{t.id}] {t.title}{assignee}{deps}")

        return ToolResult(output="\n".join(lines))
