from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from lazycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from lazycode.agent import Agent
    from lazycode.teams.manager import TeamManager


class TeamCreateParams(BaseModel):
    """定义 Agent Team 的名称和描述。"""
    team_name: str
    description: str = ""


class TeamCreateTool(Tool):
    """创建 team 存储、配置、任务板和 mailbox。"""
    name = "TeamCreate"
    description = (
        "Create a new Agent Team. This sets up a team directory, config, "
        "shared task list, and mailbox. After creating, use the Agent tool "
        "with team_name to spawn teammates."
    )
    params_model = TeamCreateParams
    category = "command"
    is_concurrency_safe = False


    def __init__(
        self,
        team_manager: TeamManager,
        parent_agent: Agent,
        teammate_mode: str = "",
        is_interactive: bool = True,
        enable_coordinator_mode: bool = False,
    ) -> None:
        """保存创建 team 所需的父代理和运行模式配置。"""
        self._team_manager = team_manager
        self._parent_agent = parent_agent
        self._teammate_mode = teammate_mode
        self._is_interactive = is_interactive
        self._enable_coordinator_mode = enable_coordinator_mode


    async def execute(self, params: BaseModel) -> ToolResult:
        """检测后端、创建 team，并按需启用协调模式。"""
        p: TeamCreateParams = params  # type: ignore[assignment]

        from lazycode.teams.backend_detect import BackendDetectionError

        try:
            backend = self._team_manager.detect_backend(
                self._teammate_mode, self._is_interactive
            )
        except BackendDetectionError as e:
            return ToolResult(output=str(e), is_error=True)

        try:
            team = self._team_manager.create_team(
                name=p.team_name,
                lead_agent_id=self._parent_agent.agent_id,
                description=p.description,
                teammate_mode=self._teammate_mode,
                is_interactive=self._is_interactive,
            )
        except Exception as e:
            return ToolResult(output=f"Failed to create team: {e}", is_error=True)

        coordinator_note = ""
        from lazycode.teams.coordinator import is_coordinator_mode
        if is_coordinator_mode(self._enable_coordinator_mode):
            from lazycode.agents.tool_filter import apply_coordinator_filter
            self._parent_agent.coordinator_mode = True
            self._parent_agent._team_manager = self._team_manager
            self._parent_agent._full_registry = self._parent_agent.registry
            self._parent_agent.registry = apply_coordinator_filter(self._parent_agent.registry)
            coordinator_note = "\nCoordinator Mode activated: tools narrowed to dispatch-only."

        return ToolResult(
            output=(
                f"Team '{team.name}' created successfully.\n"
                f"Backend: {backend.value}\n"
                f"Config: {team.config_path}\n"
                f"Use Agent tool with team_name='{team.name}' to spawn teammates."
                f"{coordinator_note}"
            )
        )
