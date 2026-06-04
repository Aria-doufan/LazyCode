from __future__ import annotations

import logging

from lazycode.config import MCPServerConfig
from lazycode.mcp.client import MCPClient
from lazycode.mcp.tool_wrapper import MCPToolWrapper
from lazycode.tools import ToolRegistry

logger = logging.getLogger(__name__)


class MCPManager:


    """管理多个 MCP 服务；把外部服务注册成本地工具。"""
    def __init__(self) -> None:
        """分别保存配置和已连接客户端，便于失败后按需重建连接。"""
        self._configs: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, MCPClient] = {}


    def load_configs(self, configs: list[MCPServerConfig]) -> None:
        """按名称索引配置；同名配置以后加载的为准，便于本地覆盖。"""
        for cfg in configs:
            self._configs[cfg.name] = cfg


    async def register_all_tools(self, registry: ToolRegistry) -> list[str]:
        """连接所有 MCP 服务并注册工具；单个服务失败不阻塞主应用启动。"""
        errors: list[str] = []
        for name, config in self._configs.items():
            try:
                client = MCPClient(config)
                await client.connect()
                self._clients[name] = client

                tools = await client.list_tools()
                for tool_def in tools:
                    # 每个远端工具包一层本地 Tool，复用现有权限、defer 和 schema 流程。
                    wrapper = MCPToolWrapper(name, tool_def, client)
                    registry.register(wrapper)
                    logger.info("Registered MCP tool: %s", wrapper.name)

            except Exception as e:
                msg = f"MCP server '{name}': {e}"
                logger.warning(msg)
                errors.append(msg)

        return errors


    async def get_client(self, name: str) -> MCPClient | None:
        """按需获取可用客户端；断线时重建，避免复用失效会话。"""
        client = self._clients.get(name)
        if client is None:
            config = self._configs.get(name)
            if config is None:
                return None
            client = MCPClient(config)
            await client.connect()
            self._clients[name] = client
            return client

        if not client.is_alive:
            logger.info("Reconnecting MCP server '%s'", name)
            await client.close()
            client = MCPClient(self._configs[name])
            await client.connect()
            self._clients[name] = client

        return client


    async def shutdown(self) -> None:
        """退出时逐个关闭客户端；某个服务关闭失败不影响其它服务释放。"""
        for name, client in self._clients.items():
            try:
                await client.close()
                logger.info("MCP server '%s' closed", name)
            except Exception:
                logger.debug("Error closing MCP server '%s'", name, exc_info=True)
        self._clients.clear()
