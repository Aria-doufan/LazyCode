from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from lazycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from lazycode.agent import Agent
    from lazycode.teams.manager import TeamManager


class TeamDeleteParams(BaseModel):
    """定义要删除的 Agent Team 名称。"""
    team_name: str


class TeamDeleteTool(Tool):
    """删除 Agent Team 并清理相关进程和目录。"""
    name = "TeamDelete"
    description = (
        "Delete an Agent Team. Terminates all pane processes, removes worktrees, "
        "cleans up mailbox and team directory. Requires all members to be idle."
    )
    params_model = TeamDeleteParams
    category = "command"
    is_concurrency_safe = False


    def __init__(self, team_manager: TeamManager, parent_agent: Agent | None = None) -> None:
        """保存 team 管理器和可选父代理。"""
        self._team_manager = team_manager
        self._parent_agent = parent_agent


    async def execute(self, params: BaseModel) -> ToolResult:
        """删除 team，并在需要时恢复协调模式前的完整工具集。"""
        p: TeamDeleteParams = params  # type: ignore[assignment]

        from lazycode.teams.manager import TeamError

        try:
            self._team_manager.delete_team(p.team_name)
        except TeamError as e:
            return ToolResult(output=str(e), is_error=True)
        except Exception as e:
            return ToolResult(output=f"Failed to delete team: {e}", is_error=True)

        coordinator_note = ""
        if self._parent_agent and self._parent_agent.coordinator_mode:
            full_registry = getattr(self._parent_agent, '_full_registry', None)
            if full_registry is not None:
                self._parent_agent.registry = full_registry
                self._parent_agent._full_registry = None
            self._parent_agent.coordinator_mode = False
            coordinator_note = "\nCoordinator Mode deactivated: full tools restored."

        return ToolResult(output=f"Team '{p.team_name}' deleted successfully.{coordinator_note}")
