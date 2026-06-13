from __future__ import annotations

import hashlib
import os
import time
import tokenize
from pathlib import Path

from lazycode.codegraph.extractor import extract_python_graph
from lazycode.codegraph.models import EdgeRecord, FileRecord
from lazycode.codegraph.store import CodeGraphStore

SKIP_DIRS = {".git", ".lazycode", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


def default_db_path(project_root: Path) -> Path:
    root = project_root.resolve()
    db_dir = root / ".lazycode"
    try:
        resolved_db_dir = db_dir.resolve(strict=False)
        resolved_db_dir.relative_to(root)
    except OSError as exc:
        raise ValueError(f"resolving CodeGraph database path failed: {exc}") from exc
    except ValueError as exc:
        raise ValueError("CodeGraph database path must remain inside project root") from exc
    return db_dir / "codegraph.sqlite"


def stale_index_paths(project_root: Path, store: CodeGraphStore) -> list[str]:
    root = project_root.resolve()
    stale_paths: list[str] = []
    for record in store.get_files():
        record_path = Path(record.path)
        if record_path.is_absolute() or ".." in record_path.parts:
            stale_paths.append(record.path)
            continue

        path = (root / record_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            stale_paths.append(record.path)
            continue

        try:
            if not path.is_file():
                stale_paths.append(record.path)
                continue
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            stale_paths.append(record.path)
            continue

        if content_hash != record.content_hash:
            stale_paths.append(record.path)

    return stale_paths


class CodeGraphIndexer:
    def __init__(self, project_root: Path, store: CodeGraphStore) -> None:
        self.project_root = project_root.resolve()
        self.store = store

    def index_all(self) -> dict[str, int]:
        indexed = 0
        skipped = 0
        python_files = self._iter_python_files()
        current_paths = {path.relative_to(self.project_root).as_posix() for path in python_files}
        removed_paths = [
            record.path for record in self.store.get_files() if record.path not in current_paths
        ]
        removed = self.store.delete_files(removed_paths)

        for path in python_files:
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

        self.resolve_references()
        return {"indexed": indexed, "skipped": skipped, "removed": removed}

    def resolve_references(self) -> int:
        edges: list[EdgeRecord] = []
        for ref in self.store.get_unresolved_refs():
            if not ref.is_resolvable or "." in ref.name:
                continue
            target = self.store.find_callable_by_name(ref.name)
            if target is None:
                continue
            edges.append(
                EdgeRecord(
                    source=ref.source,
                    target=target.id,
                    kind=ref.kind,
                    line=ref.line,
                    col=ref.col,
                    metadata={"name": ref.name, "generated_by": "resolve_references"},
                )
            )

        self.store.replace_resolved_call_edges(edges)
        return len(edges)

    def _iter_python_files(self) -> list[Path]:
        files: list[Path] = []
        for root, dirs, filenames in os.walk(self.project_root):
            dirs[:] = sorted(dirname for dirname in dirs if dirname not in SKIP_DIRS)
            root_path = Path(root)
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = root_path / filename
                try:
                    path.resolve().relative_to(self.project_root)
                except (OSError, ValueError):
                    continue
                files.append(path)
        return files

    def _read_python_source(self, path: Path) -> str:
        with tokenize.open(path) as source_file:
            return source_file.read()
