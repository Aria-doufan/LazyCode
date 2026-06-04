from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from lazycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from lazycode.cache import FileCache


class Params(BaseModel):
    """定义写入文件所需的路径和内容。"""
    file_path: str = Field(description="Path to the file to write")
    content: str = Field(description="Content to write to the file")


class WriteFile(Tool):
    """写入或覆盖文件，并按需创建父目录。"""
    name = "WriteFile"
    description = "Write content to a file, creating parent directories if needed. Overwrites existing files."
    params_model = Params
    category = "write"


    def __init__(self, file_cache: FileCache | None = None) -> None:
        """保存可选文件缓存以便写入后失效缓存。"""
        self._cache = file_cache


    async def execute(self, params: Params) -> ToolResult:
        """创建父目录、写入 UTF-8 内容并失效缓存。"""
        path = Path(params.file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content, encoding="utf-8")
            if self._cache:
                self._cache.invalidate(str(path.resolve()))
        except Exception as e:
            return ToolResult(output=f"Error writing file: {e}", is_error=True)
        return ToolResult(output=f"Successfully wrote to {params.file_path}")
