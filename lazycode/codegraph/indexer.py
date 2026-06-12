from __future__ import annotations

import hashlib
import time
from pathlib import Path

from lazycode.codegraph.extractor import extract_python_graph
from lazycode.codegraph.models import FileRecord
from lazycode.codegraph.store import CodeGraphStore

SKIP_DIRS = {".git", ".lazycode", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


def default_db_path(project_root: Path) -> Path:
    return project_root / ".lazycode" / "codegraph.sqlite"


class CodeGraphIndexer:
    def __init__(self, project_root: Path, store: CodeGraphStore) -> None:
        self.project_root = project_root.resolve()
        self.store = store

    def index_all(self) -> dict[str, int]:
        indexed = 0
        skipped = 0

        for path in self._iter_python_files():
            source = path.read_text(encoding="utf-8")
            content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
            file_path = path.relative_to(self.project_root).as_posix()

            if self.store.get_file_hash(file_path) == content_hash:
                skipped += 1
                continue

            graph = extract_python_graph(file_path, source)
            self.store.replace_file_graph(
                FileRecord(
                    path=file_path,
                    content_hash=content_hash,
                    language="python",
                    size=len(source.encode("utf-8")),
                    indexed_at=time.time(),
                    node_count=len(graph.nodes),
                ),
                graph.nodes,
                graph.edges,
                graph.unresolved_refs,
            )
            indexed += 1

        return {"indexed": indexed, "skipped": skipped, "removed": 0}

    def _iter_python_files(self) -> list[Path]:
        files = [
            path
            for path in self.project_root.rglob("*.py")
            if not any(part in SKIP_DIRS for part in path.relative_to(self.project_root).parts)
        ]
        return sorted(files)
