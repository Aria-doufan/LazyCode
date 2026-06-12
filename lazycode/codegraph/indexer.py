from __future__ import annotations

import hashlib
import os
import time
import tokenize
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
            raw_bytes = path.read_bytes()
            source = self._read_python_source(path)
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
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
                    size=len(raw_bytes),
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
        files: list[Path] = []
        for root, dirs, filenames in os.walk(self.project_root):
            dirs[:] = sorted(dirname for dirname in dirs if dirname not in SKIP_DIRS)
            root_path = Path(root)
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    files.append(root_path / filename)
        return files

    def _read_python_source(self, path: Path) -> str:
        with tokenize.open(path) as source_file:
            return source_file.read()
