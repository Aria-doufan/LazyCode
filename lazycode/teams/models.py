from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class BackendType(str, Enum):
    """封装后端 Type相关状态和行为。"""
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"


@dataclass
class TeammateInfo:
    """保存Teammate信息。"""
    name: str
    agent_id: str
    agent_type: str
    model: str
    worktree_path: str
    backend_type: str  # BackendType 值
    is_active: bool | None = None

    def to_dict(self) -> dict:
        """处理to dict。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TeammateInfo:
        """处理from dict。"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _sanitize_name(name: str) -> str:
    """处理sanitize name。"""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", name.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "team"


@dataclass
class AgentTeam:
    """封装Agent team相关状态和行为。"""
    name: str
    lead_agent_id: str
    members: list[TeammateInfo] = field(default_factory=list)
    config_path: str = ""
    description: str = ""

    def get_member(self, name: str) -> TeammateInfo | None:
        """获取member。"""
        for m in self.members:
            if m.name == name or m.agent_id == name:
                return m
        return None


    def add_member(self, member: TeammateInfo) -> None:
        """处理add member。"""
        self.members.append(member)

    def remove_member(self, name: str) -> bool:
        """移除member。"""
        for i, m in enumerate(self.members):
            if m.name == name or m.agent_id == name:
                self.members.pop(i)
                return True
        return False


    def set_member_active(self, name: str, is_active: bool | None) -> bool:
        """设置member active。"""
        member = self.get_member(name)
        if member is None:
            return False
        member.is_active = is_active
        return True

    def all_idle(self) -> bool:
        """处理all idle。"""
        return all(m.is_active is False for m in self.members)


    def active_members(self) -> list[TeammateInfo]:
        """处理active members。"""
        return [m for m in self.members if m.is_active is not False]

    def to_dict(self) -> dict:
        """处理to dict。"""
        return {
            "name": self.name,
            "lead_agent_id": self.lead_agent_id,
            "members": [m.to_dict() for m in self.members],
            "config_path": self.config_path,
            "description": self.description,
        }


    @classmethod
    def from_dict(cls, data: dict) -> AgentTeam:
        """处理from dict。"""
        members = [TeammateInfo.from_dict(m) for m in data.get("members", [])]
        return cls(
            name=data["name"],
            lead_agent_id=data["lead_agent_id"],
            members=members,
            config_path=data.get("config_path", ""),
            description=data.get("description", ""),
        )

    def save(self) -> None:
        """保存save。"""
        path = Path(self.config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, config_path: str) -> AgentTeam:
        """加载load。"""
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        team = cls.from_dict(data)
        team.config_path = config_path
        return team


def resolve_team_dir(team_name: str) -> Path:
    """解析team dir。"""
    slug = _sanitize_name(team_name)
    return Path.home() / ".lazycode" / "teams" / slug


def unique_team_name(team_name: str) -> str:
    """处理unique team name。"""
    slug = _sanitize_name(team_name)
    base_dir = Path.home() / ".lazycode" / "teams"
    if not (base_dir / slug).exists():
        return slug
    counter = 2
    while (base_dir / f"{slug}-{counter}").exists():
        counter += 1
    return f"{slug}-{counter}"
