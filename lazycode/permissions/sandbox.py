from __future__ import annotations

import tempfile
from pathlib import Path


class PathSandbox:


    """封装路径 沙箱相关状态和行为。"""
    def __init__(
        self,
        project_root: str,
        extra_allowed: list[str] | None = None,
    ) -> None:
        """初始化路径 沙箱实例。"""
        root = Path(project_root).resolve()
        self._allowed_roots: list[Path] = [root, Path(tempfile.gettempdir()).resolve()]
        if extra_allowed:
            for p in extra_allowed:
                self._allowed_roots.append(Path(p).resolve())


    @property
    def project_root(self) -> Path:
        """处理project root。"""
        return self._allowed_roots[0]


    def check(self, path: str) -> tuple[bool, str]:
        """处理check。"""
        p = Path(path).expanduser()
        original_is_absolute = p.is_absolute() or path.startswith(("/", "\\"))
        if not original_is_absolute:
            p = self.project_root / p
        abs_path = p.absolute()

        try:
            real_path = abs_path.resolve(strict=True)
        except OSError:
            parent = abs_path.parent
            try:
                parent_real = parent.resolve(strict=True)
            except OSError:
                if original_is_absolute:
                    real_path = abs_path
                else:
                    return False, f"无法解析路径: {path}"
            else:
                real_path = parent_real / abs_path.name

        for root in self._allowed_roots:
            try:
                real_path.relative_to(root)
                return True, ""
            except ValueError:
                continue

        return False, f"路径 {path} 超出沙箱范围"
