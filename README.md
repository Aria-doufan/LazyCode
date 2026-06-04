# LazyCode

LazyCode 是一个 Python 实现的终端 AI Coding Agent，用于学习和实践 Agent 工具调用、权限控制、上下文管理、技能系统和多 Agent 协作等工程能力。

## 功能概览

- 终端交互式 AI Coding Agent
- 工具调用与权限校验
- 技能加载与执行
- 会话、上下文和记忆管理
- 多 Agent / Team 协作能力
- Git worktree 集成

## 环境要求

- Python 3.11+
- uv

## 快速开始

```bash
uv sync
uv run lazycode
```

## 配置

本项目不会提交本地配置和密钥文件。运行前请在本地创建 `.lazycode/config.yaml`，并填入自己的模型提供商、API Key 和 MCP Server 配置。

## 运行测试

```bash
uv run pytest
```
