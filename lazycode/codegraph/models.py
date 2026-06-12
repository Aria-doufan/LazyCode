from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

NodeKind = Literal[
    "module",
    "class",
    "function",
    "method",
    "import",
]

EdgeKind = Literal[
    "contains",
    "imports",
    "calls",
]


@dataclass(frozen=True)
class FileRecord:
    path: str
    content_hash: str
    language: str
    size: int
    indexed_at: float
    node_count: int


@dataclass(frozen=True)
class NodeRecord:
    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    signature: str = ""


@dataclass(frozen=True)
class EdgeRecord:
    source: str
    target: str
    kind: EdgeKind
    line: int = 0
    col: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UnresolvedReference:
    source: str
    name: str
    kind: EdgeKind
    line: int
    col: int
    file_path: str
