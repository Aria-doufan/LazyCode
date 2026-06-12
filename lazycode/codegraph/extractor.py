from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from lazycode.codegraph.models import EdgeRecord, NodeRecord, UnresolvedReference


@dataclass
class ExtractionResult:
    nodes: list[NodeRecord] = field(default_factory=list)
    edges: list[EdgeRecord] = field(default_factory=list)
    unresolved_refs: list[UnresolvedReference] = field(default_factory=list)


def extract_python_graph(file_path: str, source: str) -> ExtractionResult:
    tree = ast.parse(source, filename=file_path)
    result = ExtractionResult()
    visitor = _PythonGraphVisitor(file_path, source, result)
    visitor.extract(tree)
    return result


def _module_name(file_path: str) -> str:
    path = PurePosixPath(file_path.replace("\\", "/"))
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _PythonGraphVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, source: str, result: ExtractionResult) -> None:
        self.file_path = file_path
        self.source = source
        self.result = result
        self.module_name = _module_name(file_path)
        self.scope_stack: list[tuple[str, str, bool]] = []

    def extract(self, tree: ast.Module) -> None:
        module_id = f"{self.file_path}::module"
        end_line = max(1, len(self.source.splitlines()))
        self.result.nodes.append(
            NodeRecord(
                id=module_id,
                kind="module",
                name=self.module_name.rsplit(".", 1)[-1],
                qualified_name=self.module_name,
                file_path=self.file_path,
                language="python",
                start_line=1,
                end_line=end_line,
                signature=f"module {self.module_name}",
            )
        )
        self.scope_stack.append((module_id, "", False))
        self.visit(tree)
        self.scope_stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            self._add_import_node(name, node.lineno, getattr(node, "end_lineno", node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            self._add_import_node(name, node.lineno, getattr(node, "end_lineno", node.lineno))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent_id, parent_qualified, _ = self.scope_stack[-1]
        qualified = self._qualify(parent_qualified, node.name)
        node_id = f"{self.file_path}::{qualified}"
        self.result.nodes.append(
            NodeRecord(
                id=node_id,
                kind="class",
                name=node.name,
                qualified_name=qualified,
                file_path=self.file_path,
                language="python",
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                signature=f"class {node.name}",
            )
        )
        self._add_contains_edge(parent_id, node_id, node)
        self.scope_stack.append((node_id, qualified, True))
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_function_node(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_function_node(node, is_async=True)

    def _add_function_node(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> None:
        parent_id, parent_qualified, parent_is_class = self.scope_stack[-1]
        qualified = self._qualify(parent_qualified, node.name)
        node_id = f"{self.file_path}::{qualified}"
        self.result.nodes.append(
            NodeRecord(
                id=node_id,
                kind="method" if parent_is_class else "function",
                name=node.name,
                qualified_name=qualified,
                file_path=self.file_path,
                language="python",
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                signature=self._function_signature(node, is_async=is_async),
            )
        )
        self._add_contains_edge(parent_id, node_id, node)
        self.scope_stack.append((node_id, qualified, False))
        self.generic_visit(node)
        self.scope_stack.pop()

    def _add_import_node(self, name: str, start_line: int, end_line: int) -> None:
        parent_id, _, _ = self.scope_stack[-1]
        qualified = f"{self.module_name}.{name}" if self.module_name else name
        node_id = f"{self.file_path}::{qualified}"
        self.result.nodes.append(
            NodeRecord(
                id=node_id,
                kind="import",
                name=name,
                qualified_name=qualified,
                file_path=self.file_path,
                language="python",
                start_line=start_line,
                end_line=end_line,
                signature=f"import {name}",
            )
        )
        self.result.edges.append(
            EdgeRecord(
                source=parent_id,
                target=node_id,
                kind="contains",
                line=start_line,
                metadata={},
            )
        )

    def _add_contains_edge(self, source: str, target: str, node: ast.AST) -> None:
        self.result.edges.append(
            EdgeRecord(
                source=source,
                target=target,
                kind="contains",
                line=getattr(node, "lineno", 0),
                col=getattr(node, "col_offset", 0),
                metadata={},
            )
        )

    def _function_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> str:
        args = ", ".join(self._format_arg(arg) for arg in [*node.args.posonlyargs, *node.args.args])
        prefix = "async def" if is_async else "def"
        signature = f"{prefix} {node.name}({args})"
        if node.returns is not None:
            signature += f" -> {ast.unparse(node.returns)}"
        return signature

    def _format_arg(self, arg: ast.arg) -> str:
        if arg.annotation is None:
            return arg.arg
        return f"{arg.arg}: {ast.unparse(arg.annotation)}"

    def _qualify(self, parent_qualified: str, name: str) -> str:
        if not parent_qualified:
            return name
        return f"{parent_qualified}.{name}"
