from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from lazycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from lazycode.agent import Agent
    from lazycode.skills.directory import register_skill_tools
    from lazycode.skills.loader import SkillLoader


class LoadSkillParams(BaseModel):
    """定义要加载的 skill 名称。"""
    name: str = Field(description="The name of the skill to load")


class LoadSkill(Tool):
    """激活 skill SOP，并注册目录型 skill 中的工具。"""
    name = "LoadSkill"
    description = (
        "Load and activate a skill by name. "
        "The skill's SOP will be pinned to the environment context "
        "and any specialized tools will be registered."
    )
    params_model = LoadSkillParams
    category = "read"
    is_concurrency_safe = False
    is_system_tool = True


    def __init__(self) -> None:
        """初始化延迟注入的 loader 和 agent 引用。"""
        self._loader: SkillLoader | None = None
        self._agent: Agent | None = None


    def set_loader(self, loader: SkillLoader) -> None:
        """注入用于查找 skill 的 loader。"""
        self._loader = loader

    def set_agent(self, agent: Agent) -> None:
        """注入用于激活 skill 的 agent。"""
        self._agent = agent


    async def execute(self, params: BaseModel) -> ToolResult:
        """查找 skill、激活 SOP 并注册目录工具。"""
        assert isinstance(params, LoadSkillParams)

        if self._loader is None or self._agent is None:
            return ToolResult(
                output="Error: LoadSkill not properly initialized",
                is_error=True,
            )

        skill = self._loader.get(params.name)
        if skill is None:
            available = ", ".join(n for n, _ in self._loader.get_catalog())
            return ToolResult(
                output=f"Error: unknown skill '{params.name}'. Available skills: {available}",
                is_error=True,
            )

        self._agent.activate_skill(skill.name, skill.prompt_body)

        tool_count = 0
        if skill.is_directory and skill.source_path is not None:
            from lazycode.skills.directory import register_skill_tools
            skill_dir = skill.source_path.parent
            tool_count = register_skill_tools(skill_dir, self._agent.registry)

        parts = [f"Skill '{skill.name}' activated. SOP pinned to environment context."]
        if tool_count > 0:
            parts.append(f"{tool_count} specialized tool(s) registered.")
        return ToolResult(output=" ".join(parts))
