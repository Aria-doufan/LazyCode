from __future__ import annotations

import tokenize
from pathlib import Path

from lazycode.codegraph.models import NodeRecord
from lazycode.codegraph.store import CodeGraphStore

MAX_SOURCE_CHARS = 6000
_TRUNCATION_MARKER = "<truncated>"


def render_node_source(
    project_root: Path,
    node: NodeRecord,
    max_chars: int = MAX_SOURCE_CHARS,
) -> str:
    project_root_path = project_root.resolve()
    source_path = (project_root_path / node.file_path).resolve()
    try:
        source_path.relative_to(project_root_path)
    except ValueError:
        return f"Error: source path escapes project root: {node.file_path}"

    try:
        if not source_path.is_file():
            return f"Error: source file unavailable: {node.file_path}"
        with tokenize.open(source_path) as source_file:
            lines = source_file.read().splitlines()
    except (OSError, SyntaxError, UnicodeError) as exc:
        return f"Error: source file unavailable: {node.file_path} ({exc})"

    start_line = max(node.start_line, 1)
    end_line = max(node.end_line, start_line)

    rendered_lines = [
        f"{line_no}\t{lines[line_no - 1]}"
        for line_no in range(start_line, min(end_line, len(lines)) + 1)
    ]
    output = "\n".join(rendered_lines)
    return _truncate_source(output, max_chars)


def _truncate_source(output: str, max_chars: int) -> str:
    if len(output) <= max_chars:
        return output
    if max_chars <= 0:
        return ""
    if max_chars >= len(_TRUNCATION_MARKER) + 1:
        suffix = f"\n{_TRUNCATION_MARKER}"
        return f"{output[: max_chars - len(suffix)]}{suffix}"
    if max_chars >= len(_TRUNCATION_MARKER):
        return _TRUNCATION_MARKER
    return output[:max_chars]


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
    rendered_ranges: set[tuple[str, int, int]] = set()
    for node in nodes:
        location = _format_node_location(node)
        symbol_lines.append(f"- {location}")
        source_range = (node.file_path, node.start_line, node.end_line)
        if source_range in rendered_ranges:
            continue
        rendered_ranges.add(source_range)
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
