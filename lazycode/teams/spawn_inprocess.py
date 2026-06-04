from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lazycode.agent import Agent
    from lazycode.conversation import ConversationManager

log = logging.getLogger(__name__)


class InProcessTeammateHandle:
    """封装In Process Teammate Handle相关状态和行为。"""
    def __init__(
        self,
        agent: Agent,
        task: asyncio.Task[str],
        name: str,
    ) -> None:
        """初始化In Process Teammate Handle实例。"""
        self.agent = agent
        self.task = task
        self.name = name


    @property
    def done(self) -> bool:
        """处理done。"""
        return self.task.done()

    @property
    def result(self) -> str | None:
        """处理结果。"""
        if self.task.done():
            try:
                return self.task.result()
            except (asyncio.CancelledError, Exception):
                return None
        return None


    def cancel(self) -> None:
        """取消cancel。"""
        if not self.task.done():
            self.task.cancel()


def spawn_inprocess_teammate(
    agent: Agent,
    prompt: str,
    name: str,
    conversation: ConversationManager | None = None,
) -> InProcessTeammateHandle:


    """处理spawn inprocess teammate。"""
    async def _run() -> str:
        """运行run。"""
        if conversation is not None:
            return await agent.run_to_completion("", conversation)
        return await agent.run_to_completion(prompt)

    task = asyncio.create_task(_run(), name=f"teammate-{name}")
    log.info("Spawned in-process teammate %s", name)
    return InProcessTeammateHandle(agent=agent, task=task, name=name)
