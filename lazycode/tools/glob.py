from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from lazycode.tools.base import SKIP_DIRS, Tool, ToolResult


class Params(BaseModel):
    """定义文件发现使用的 glob 模式和基础目录。"""
    pattern: str = Field(description="Glob pattern to match (e.g. '**/*.py')")
    path: str = Field(default=".", description="Base directory to search from")


class Glob(Tool):
    """查找匹配 glob 模式的文件。"""
    name = "Glob"
    description = "Find files matching a glob pattern, returning relative paths."
    params_model = Params
    category = "read"
    is_concurrency_safe = True


    async def execute(self, params: Params) -> ToolResult:
        """搜索匹配文件并跳过忽略目录。"""
        base = Path(params.path)
        if not base.exists():
            return ToolResult(output=f"Error: path not found: {params.path}", is_error=True)

        try:
            matches = sorted(
                str(p.relative_to(base))
                for p in base.glob(params.pattern)
                if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)
            )
        except Exception as e:
            return ToolResult(output=f"Error: {e}", is_error=True)

        if not matches:
            return ToolResult(output="No files matched the pattern.")
        return ToolResult(output="\n".join(matches))

