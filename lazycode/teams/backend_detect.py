from __future__ import annotations

import os
import shutil

from lazycode.teams.models import BackendType


class BackendDetectionError(Exception):
    """表示后端 Detection异常。"""
    pass


def _in_tmux_session() -> bool:
    """处理in tmux 会话。"""
    return bool(os.environ.get("TMUX"))


def _in_iterm2() -> bool:
    """处理in iterm 2。"""
    return os.environ.get("TERM_PROGRAM") == "iTerm.app"


def _it2_available() -> bool:
    """处理it 2 available。"""
    return shutil.which("it2") is not None


def _tmux_installed() -> bool:
    """处理tmux installed。"""
    return shutil.which("tmux") is not None


def detect_backend(
    teammate_mode: str = "",
    is_interactive: bool = True,
) -> BackendType:
    """检测后端。"""
    if teammate_mode == "in-process" or not is_interactive:
        return BackendType.IN_PROCESS

    if _in_tmux_session():
        return BackendType.TMUX

    if _in_iterm2() and _it2_available():
        return BackendType.ITERM2

    if _tmux_installed():
        return BackendType.TMUX

    raise BackendDetectionError(
        "No suitable terminal backend found for Agent Team.\n"
        "Install one of the following:\n"
        "  - tmux: brew install tmux\n"
        "  - iTerm2 + it2 CLI: https://iterm2.com/utilities/it2check\n"
        "Or set 'teammate_mode: \"in-process\"' in config.yaml to use in-process backend."
    )
