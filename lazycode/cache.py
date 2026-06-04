from __future__ import annotations

import threading


class FileCache:
    """封装文件 缓存相关状态和行为。"""
    def __init__(self) -> None:
        """初始化文件 缓存实例。"""
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> str | None:
        """获取get。"""
        with self._lock:
            return self._store.get(path)


    def put(self, path: str, content: str) -> None:
        """处理put。"""
        with self._lock:
            self._store[path] = content


    def invalidate(self, path: str) -> None:
        """处理invalidate。"""
        with self._lock:
            self._store.pop(path, None)


    def clear(self) -> None:
        """清理clear。"""
        with self._lock:
            self._store.clear()


    def __len__(self) -> int:
        """处理len。"""
        with self._lock:
            return len(self._store)
