from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class TraceNode:
    """封装trace Node相关状态和行为。"""
    agent_id: str
    parent_id: str | None
    trace_id: str
    agent_type: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    status: str = "running"


class TraceManager:
    """管理trace。"""
    def __init__(self) -> None:
        """初始化trace 管理器实例。"""
        self._nodes: dict[str, TraceNode] = {}


    def create(
        self,
        agent_type: str,
        parent_id: str | None = None,
        trace_id: str | None = None,
    ) -> TraceNode:
        """创建create。"""
        agent_id = uuid.uuid4().hex[:12]
        if trace_id is None:
            trace_id = uuid.uuid4().hex[:12]

        node = TraceNode(
            agent_id=agent_id,
            parent_id=parent_id,
            trace_id=trace_id,
            agent_type=agent_type,
        )
        self._nodes[agent_id] = node
        return node

    def update(self, agent_id: str, **kwargs: int | str) -> None:
        """更新update。"""
        node = self._nodes.get(agent_id)
        if node is None:
            return
        for key, value in kwargs.items():
            if hasattr(node, key):
                setattr(node, key, value)


    def complete(self, agent_id: str, status: str = "completed") -> None:
        """处理complete。"""
        node = self._nodes.get(agent_id)
        if node is None:
            return
        node.end_time = time.monotonic()
        node.status = status


    def get(self, agent_id: str) -> TraceNode | None:
        """获取get。"""
        return self._nodes.get(agent_id)

    def get_tree(self, trace_id: str) -> list[TraceNode]:
        """获取tree。"""
        return [n for n in self._nodes.values() if n.trace_id == trace_id]


    def remove(self, agent_id: str) -> None:
        """移除remove。"""
        self._nodes.pop(agent_id, None)

    def complete_all_running(self, parent_id: str) -> None:
        """处理complete all running。"""
        for node in self._nodes.values():
            if node.parent_id == parent_id and node.status == "running":
                node.status = "completed"
                node.end_time = time.monotonic()

    def get_total_tokens(self, trace_id: str) -> tuple[int, int]:
        """获取total token。"""
        total_in = 0
        total_out = 0
        for node in self._nodes.values():
            if node.trace_id == trace_id:
                total_in += node.input_tokens
                total_out += node.output_tokens
        return total_in, total_out
