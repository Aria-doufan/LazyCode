from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from lazycode.config import MCPServerConfig, build_child_env, resolve_env_vars

logger = logging.getLogger(__name__)


class MCPClient:
    """维护单个 MCP 服务连接；把 stdio/HTTP 差异收敛成统一会话。"""
    def __init__(self, config: MCPServerConfig) -> None:
        """保存配置和连接状态，避免每次工具调用都重复建连。"""
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._alive = False


    @property
    def is_alive(self) -> bool:
        """判断alive是否成立。"""
        return self._alive


    async def connect(self) -> None:
        """建立 MCP 会话；已连接时直接复用，避免重复初始化远端服务。"""
        if self._alive:
            return

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        try:
            if self.config.is_stdio:
                read, write = await self._connect_stdio()
            else:
                read, write = await self._connect_http()

            # MCP SDK 只关心读写流；上层不需要知道服务来自子进程还是 HTTP。
            session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            self._session = session
            self._alive = True
            logger.info("MCP server '%s' connected", self.name)
        except Exception:
            # 初始化失败时必须清掉半开的进程/连接，否则下次重连会继承脏状态。
            await self._cleanup_stack()
            raise


    async def _connect_stdio(self) -> tuple[Any, Any]:
        """启动本地 MCP 子进程；只传声明的环境变量，避免泄露宿主密钥。"""
        assert self._stack is not None
        assert self.config.command is not None

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=build_child_env(self.config.env),
        )
        read, write = await self._stack.enter_async_context(
            stdio_client(params)
        )
        return read, write

    async def _connect_http(self) -> tuple[Any, Any]:
        """连接远程 MCP 服务；运行时解析 header 环境变量，支持密钥轮换。"""
        assert self._stack is not None
        assert self.config.url is not None

        resolved_headers = {
            k: resolve_env_vars(v) for k, v in self.config.headers.items()
        }
        http_client = httpx.AsyncClient(
            headers=resolved_headers,
            follow_redirects=True,
        )
        # HTTP client 和 MCP stream 共享同一个退出栈，保证关闭顺序与创建顺序相反。
        await self._stack.enter_async_context(http_client)

        result = await self._stack.enter_async_context(
            streamable_http_client(self.config.url, http_client=http_client)
        )
        read, write = result[0], result[1]
        return read, write


    async def list_tools(self) -> list[types.Tool]:
        """拉取远端工具清单，用于注册成本地 ToolRegistry 工具。"""
        assert self._session is not None
        result = await self._session.list_tools()
        return list(result.tools)


    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        """通过已初始化的会话调用远端工具，保持协议错误原样向上传递。"""
        assert self._session is not None
        return await self._session.call_tool(name, arguments)

    async def close(self) -> None:
        """关闭会话并标记不可用，让下一次调用能触发干净重连。"""
        self._alive = False
        self._session = None
        await self._cleanup_stack()

    async def _cleanup_stack(self) -> None:
        """集中释放退出栈，避免 stdio 进程和 HTTP 连接分散清理。"""
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug("Cancel scope cleanup (expected during shutdown): %s", e)
                else:
                    raise
            except Exception:
                logger.debug("Error closing stack for '%s'", self.name, exc_info=True)
            self._stack = None
