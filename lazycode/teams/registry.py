from __future__ import annotations

import threading


class AgentNameRegistry:
    """维护Agent Name注册表。"""
    _instance: AgentNameRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        """初始化Agent Name 注册表实例。"""
        self._names: dict[str, str] = {}  # name -> agent_id


    @classmethod
    def instance(cls) -> AgentNameRegistry:
        """处理instance。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


    @classmethod
    def reset(cls) -> None:
        """重置reset。"""
        with cls._lock:
            cls._instance = None


    def register(self, name: str, agent_id: str) -> None:
        """注册register。"""
        self._names[name] = agent_id

    def resolve(self, name_or_id: str) -> str | None:
        """解析resolve。"""
        if name_or_id in self._names:
            return self._names[name_or_id]
        if name_or_id in self._names.values():
            return name_or_id
        return None

    def unregister(self, name: str) -> None:
        """注销unregister。"""
        self._names.pop(name, None)


    def list_all(self) -> dict[str, str]:
        """列出all。"""
        return dict(self._names)
