
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from lazycode.permissions.dangerous import DangerousCommandDetector, is_safe_command
from lazycode.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from lazycode.permissions.rules import RuleEngine, extract_content
from lazycode.permissions.sandbox import PathSandbox
from lazycode.tools.base import Tool

_PLAN_MODE_ALLOWED_TOOLS = frozenset({"Agent", "ToolSearch", "AskUserQuestion"})


@dataclass
class Decision:
    """封装Decision相关状态和行为。"""
    effect: DecisionEffect
    reason: str


class PermissionChecker:


    """封装权限 Checker相关状态和行为。"""
    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        """初始化权限 Checker实例。"""
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path: str = ""


    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        """处理check。"""
        content = extract_content(tool.name, arguments)

        if self.mode == PermissionMode.PLAN:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
                return Decision(effect="allow", reason="Plan mode: allowed tool")
            if tool.name in ("WriteFile", "EditFile"):
                if content and self._is_plan_file(content):
                    return Decision(effect="allow", reason="Plan mode: plan file write")
                return Decision(effect="deny", reason="Plan mode: write tools are restricted")

        if tool.category == "command" and is_safe_command(content or ""):
            return Decision(effect="allow", reason="Safe read-only command")

        if tool.category == "command":
            hit, reason = self.detector.detect(content)
            if hit:
                return Decision(effect="deny", reason=f"危险命令拦截: {reason}")

        if tool.category in ("read", "write") and content:
            ok, reason = self.sandbox.check(content)
            if not ok:
                return Decision(effect="deny", reason=f"路径沙箱拦截: {reason}")

        rule_result = self.rule_engine.evaluate(tool.name, content)
        if rule_result == "allow":
            return Decision(effect="allow", reason="权限规则放行")
        if rule_result == "deny":
            return Decision(effect="deny", reason="权限规则拒绝")

        # Layer 4：permission mode
        effect = mode_decide(self.mode, tool.category)
        if effect == "allow":
            return Decision(effect="allow", reason=f"权限模式 {self.mode.value} 放行")
        if effect == "deny":
            return Decision(effect="deny", reason=f"权限模式 {self.mode.value} 拒绝")

        # Layer 5: ASK → triggers HITL
        return Decision(effect="ask", reason="需要用户确认")


    def _is_plan_file(self, target_path: str) -> bool:
        """判断plan 文件是否成立。"""
        if not self.plan_file_path or not target_path:
            return ".lazycode/plans/" in target_path
        try:
            abs_target = os.path.abspath(target_path)
            abs_plan = os.path.abspath(self.plan_file_path)
            if abs_target == abs_plan:
                return True
        except Exception:
            pass
        if os.path.basename(target_path) == os.path.basename(self.plan_file_path):
            return True
        return ".lazycode/plans/" in target_path

'''
【AI Agent 准备调用工具 (Tool + Arguments)】
                     │
                     ▼
┌────────────────────────────────────────────────────────┐
│  Layer 1: PLAN 模式特权检查                            │
│  - 是否为 PLAN 模式？                                  │
│  - 是 -> 是否为允许的工具/计划文件？                     │
└────────────────────────┬───────────────────────────────┘
                         │ ❌ 未命中特权/非PLAN模式
                         ▼
┌────────────────────────────────────────────────────────┐
│  Layer 2: 危险命令与沙箱拦截 (硬编码底线)                │
│  - Command 类别: 匹配危险正则 -> ❌ [DENY 拦截]          │
│  - Read/Write 类别: 超出路径沙箱 -> ❌ [DENY 拦截]       │
└────────────────────────┬───────────────────────────────┘
                         │  (若命中 Safe 只读命令则直接放行)
                         │ ❓ 未命中危险特征
                         ▼
┌────────────────────────────────────────────────────────┐
│  Layer 3: 动态权限规则引擎 (Rule Engine)               │
│  - 匹配自定义规则库: evaluate(name, content)            │
│  - 命中显式规则 -> ❌ [DENY 拦截] 或  [ALLOW 放行]     │
└────────────────────────┬───────────────────────────────┘
                         │ ❓ 规则库未明确提及 (None)
                         ▼
┌────────────────────────────────────────────────────────┐
│  Layer 4: 权限模式决策 (Permission Mode)               │
│  - 匹配当前运行模式 (如 DEFAULT/READ-ONLY 等)          │
│  - 命中模式限制 -> ❌ [DENY 拦截] 或  [ALLOW 放行]     │
└────────────────────────┬───────────────────────────────┘
                         │ ❓ 模式未做硬性限制
                         ▼
┌────────────────────────────────────────────────────────┐
│  Layer 5: 兜底策略 - 人类介入 (HITL)                    │
│  - 无任何硬性规则匹配，系统不盲信 AI                     │
│  - 返回 "ask" 状态 ──> 👤【触发终端弹窗，等待用户确认】 │
└────────────────────────────────────────────────────────┘
'''