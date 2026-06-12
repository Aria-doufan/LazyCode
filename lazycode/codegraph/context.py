from __future__ import annotations

from pathlib import Path

from lazycode.codegraph.models import NodeRecord
from lazycode.codegraph.store import CodeGraphStore

MAX_SOURCE_CHARS = 6000


def render_node_source(
    project_root: Path,
    node: NodeRecord,
    max_chars: int = MAX_SOURCE_CHARS,
) -> str:
    source_path = project_root / node.file_path
    lines = source_path.read_text(encoding="utf-8").splitlines()
    start_line = max(node.start_line, 1)
    end_line = max(node.end_line, start_line)

    rendered_lines = [
        f"{line_no}\t{lines[line_no - 1]}"
        for line_no in range(start_line, min(end_line, len(lines)) + 1)
    ]
    output = "\n".join(rendered_lines)
    if len(output) <= max_chars:
        return output
    return f"{output[:max_chars]}\n<truncated>"


def build_explore_context(
    project_root: Path,
    store: CodeGraphStore,
    query: str,
    max_nodes: int = 8,
) -> str:
    nodes = store.search_nodes(query, limit=max_nodes)
    if not nodes:
        dotted_query = ".".join(query.split())
        if dotted_query != query:
            nodes = store.search_nodes(dotted_query, limit=max_nodes)
    title = f"# CodeGraph Explore: {query}"
    if not nodes:
        return (
            f"{title}\n\n"
            "No indexed symbols matched. Use Grep or ReadFile for unindexed content."
        )

    symbol_lines = ["## Symbols"]
    source_sections = ["## Source"]
    for node in nodes:
        location = _format_node_location(node)
        symbol_lines.append(f"- {location}")
        source_sections.extend(
            [
                f"### {location}",
                "```",
                render_node_source(project_root, node),
                "```",
            ]
        )

    return "\n".join([title, "", *symbol_lines, "", *source_sections])


def _format_node_location(node: NodeRecord) -> str:
    return (
        f"{node.file_path}:{node.start_line}-{node.end_line} "
        f"{node.kind} {node.qualified_name}"
    )
