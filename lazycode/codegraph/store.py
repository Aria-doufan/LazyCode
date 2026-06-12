from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from lazycode.codegraph.models import (
    EdgeRecord,
    FileRecord,
    NodeRecord,
    UnresolvedReference,
)


class CodeGraphStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self._conn.close()

    def stats(self) -> dict[str, int]:
        return {
            "files": self._count("files"),
            "nodes": self._count("nodes"),
            "edges": self._count("edges"),
            "unresolved_refs": self._count("unresolved_refs"),
        }

    def get_file_hash(self, path: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        return str(row["content_hash"])

    def replace_file_graph(
        self,
        file_record: FileRecord,
        nodes: Iterable[NodeRecord],
        edges: Iterable[EdgeRecord],
        unresolved_refs: Iterable[UnresolvedReference],
    ) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM files WHERE path = ?", (file_record.path,))
            self._conn.execute(
                """
                INSERT INTO files (
                    path, content_hash, language, size, indexed_at, node_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    file_record.path,
                    file_record.content_hash,
                    file_record.language,
                    file_record.size,
                    file_record.indexed_at,
                    file_record.node_count,
                ),
            )
            self._conn.executemany(
                """
                INSERT INTO nodes (
                    id, kind, name, qualified_name, file_path, language,
                    start_line, end_line, signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        node.id,
                        node.kind,
                        node.name,
                        node.qualified_name,
                        node.file_path,
                        node.language,
                        node.start_line,
                        node.end_line,
                        node.signature,
                    )
                    for node in nodes
                ],
            )
            self._conn.executemany(
                """
                INSERT INTO edges (
                    source, target, kind, line, col, metadata
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        edge.source,
                        edge.target,
                        edge.kind,
                        edge.line,
                        edge.col,
                        json.dumps(dict(edge.metadata)),
                    )
                    for edge in edges
                ],
            )
            self._conn.executemany(
                """
                INSERT INTO unresolved_refs (
                    source, name, kind, line, col, file_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ref.source,
                        ref.name,
                        ref.kind,
                        ref.line,
                        ref.col,
                        ref.file_path,
                    )
                    for ref in unresolved_refs
                ],
            )

    def get_nodes_by_file(self, file_path: str) -> list[NodeRecord]:
        rows = self._conn.execute(
            """
            SELECT * FROM nodes
            WHERE file_path = ?
            ORDER BY start_line, end_line, name, id
            """,
            (file_path,),
        ).fetchall()
        return [self._node_from_row(row) for row in rows]

    def search_nodes(self, query: str, limit: int = 20) -> list[NodeRecord]:
        escaped_query = (
            query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped_query}%"
        rows = self._conn.execute(
            """
            SELECT * FROM nodes
            WHERE name COLLATE NOCASE LIKE ? ESCAPE '\\'
               OR qualified_name COLLATE NOCASE LIKE ? ESCAPE '\\'
               OR file_path COLLATE NOCASE LIKE ? ESCAPE '\\'
            ORDER BY name, qualified_name, file_path, id
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        ).fetchall()
        return [self._node_from_row(row) for row in rows]

    def _count(self, table: str) -> int:
        if table not in {"files", "nodes", "edges", "unresolved_refs"}:
            raise ValueError(f"Unknown table: {table}")
        row = self._conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])

    def _node_from_row(self, row: sqlite3.Row) -> NodeRecord:
        return NodeRecord(
            id=str(row["id"]),
            kind=row["kind"],
            name=str(row["name"]),
            qualified_name=str(row["qualified_name"]),
            file_path=str(row["file_path"]),
            language=str(row["language"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            signature=str(row["signature"]),
        )

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                language TEXT NOT NULL,
                size INTEGER NOT NULL,
                indexed_at REAL NOT NULL,
                node_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                language TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                signature TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                kind TEXT NOT NULL,
                line INTEGER NOT NULL DEFAULT 0,
                col INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (target) REFERENCES nodes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS unresolved_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                line INTEGER NOT NULL,
                col INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON nodes(file_path);
            CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
            CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
            CREATE INDEX IF NOT EXISTS idx_unresolved_refs_source ON unresolved_refs(source);
            CREATE INDEX IF NOT EXISTS idx_unresolved_refs_file_path ON unresolved_refs(file_path);
            """
        )
