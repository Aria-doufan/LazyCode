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


@dataclass
class _CallableScope:
    symbols: dict[str, str]
    bare_call_visible: bool
    shadowed_names: set[str] = field(default_factory=set)


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
        self.scope_stack: list[tuple[str, str, bool, bool]] = []
        self.callable_scope_stack: list[_CallableScope] = []
        self._precollected_node_ids: dict[int, str] = {}
        self._symbol_node_ids: set[str] = set()
        self._import_occurrence = 0

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
        self.scope_stack.append((module_id, "", False, False))
        module_symbols, module_shadowed_names = self._collect_module_resolution_state(tree.body)
        self.callable_scope_stack.append(
            _CallableScope(module_symbols, True, module_shadowed_names)
        )
        self.visit(tree)
        self.callable_scope_stack.pop()
        self.scope_stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            self._add_import_node(
                name,
                getattr(alias, "lineno", node.lineno),
                getattr(alias, "end_lineno", getattr(node, "end_lineno", node.lineno)),
                getattr(alias, "col_offset", node.col_offset),
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            self._add_import_node(
                name,
                getattr(alias, "lineno", node.lineno),
                getattr(alias, "end_lineno", getattr(node, "end_lineno", node.lineno)),
                getattr(alias, "col_offset", node.col_offset),
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent_id, parent_qualified, _, _ = self.scope_stack[-1]
        qualified = self._qualify(parent_qualified, node.name)
        node_id = self._unique_symbol_node_id(f"{self.file_path}::{qualified}", node)
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
        self.scope_stack.append((node_id, qualified, True, False))
        self.callable_scope_stack.append(
            _CallableScope(self._collect_callable_symbols(node.body, qualified), False)
        )
        self.generic_visit(node)
        self.callable_scope_stack.pop()
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_function_node(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_function_node(node, is_async=True)

    def _add_function_node(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> None:
        parent_id, parent_qualified, parent_is_class, _ = self.scope_stack[-1]
        qualified = self._qualify(parent_qualified, node.name)
        node_id = self._node_id_for(node, qualified)
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
        self.scope_stack.append((node_id, qualified, False, True))
        self.callable_scope_stack.append(
            _CallableScope(
                self._collect_callable_symbols(node.body, qualified),
                True,
                self._collect_local_shadowed_names(node),
            )
        )
        for statement in node.body:
            self.visit(statement)
        self.callable_scope_stack.pop()
        self.scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        current_id, _, _, in_callable = self.scope_stack[-1]
        if in_callable:
            call_name = self._call_name(node.func)
            target_id = self._resolve_callable(call_name)
            if target_id is not None:
                self.result.edges.append(
                    EdgeRecord(
                        source=current_id,
                        target=target_id,
                        kind="calls",
                        line=getattr(node, "lineno", 0),
                        col=getattr(node, "col_offset", 0),
                        metadata={"name": call_name},
                    )
                )
            elif call_name:
                self.result.unresolved_refs.append(
                    UnresolvedReference(
                        source=current_id,
                        name=call_name,
                        kind="calls",
                        line=getattr(node, "lineno", 0),
                        col=getattr(node, "col_offset", 0),
                        file_path=self.file_path,
                        is_resolvable=self._is_resolvable_unresolved_reference(call_name),
                    )
                )
        self.generic_visit(node)

    def _collect_callable_symbols(self, body: list[ast.stmt], parent_qualified: str) -> dict[str, str]:
        symbols: dict[str, str] = {}
        for statement in body:
            self._collect_callable_symbol_from_statement(statement, parent_qualified, symbols)
        return symbols

    def _collect_module_resolution_state(
        self, body: list[ast.stmt]
    ) -> tuple[dict[str, str], set[str]]:
        final_bindings: dict[str, str | None] = {}
        unsafe_bindings: dict[str, bool] = {}
        for statement in body:
            self._collect_module_binding_from_statement(
                statement, final_bindings, unsafe_bindings
            )
        symbols = {
            name: node_id for name, node_id in final_bindings.items() if node_id is not None
        }
        shadowed_names = {
            name
            for name, node_id in final_bindings.items()
            if node_id is None and unsafe_bindings.get(name, False)
        }
        return symbols, shadowed_names

    def _collect_module_binding_from_statement(
        self,
        statement: ast.AST,
        final_bindings: dict[str, str | None],
        unsafe_bindings: dict[str, bool],
    ) -> None:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            qualified = self._qualify("", statement.name)
            node_id = self._unique_symbol_node_id(f"{self.file_path}::{qualified}", statement)
            self._precollected_node_ids[id(statement)] = node_id
            final_bindings[statement.name] = node_id
            unsafe_bindings[statement.name] = False
            return
        if isinstance(statement, ast.ClassDef):
            final_bindings[statement.name] = None
            unsafe_bindings[statement.name] = True
            return
        if isinstance(statement, ast.ImportFrom) and any(alias.name == "*" for alias in statement.names):
            for name in list(final_bindings):
                final_bindings[name] = None
                unsafe_bindings[name] = True
            return

        for name in self._collect_direct_module_binding_names(statement):
            final_bindings[name] = None
            unsafe_bindings[name] = True
        for name in self._collect_direct_module_import_names(statement):
            final_bindings[name] = None
            unsafe_bindings[name] = False

        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt | ast.ExceptHandler | ast.match_case):
                self._collect_module_binding_from_statement(
                    child, final_bindings, unsafe_bindings
                )

    def _collect_callable_symbol_from_statement(
        self, statement: ast.AST, parent_qualified: str, symbols: dict[str, str]
    ) -> None:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            qualified = self._qualify(parent_qualified, statement.name)
            node_id = self._unique_symbol_node_id(f"{self.file_path}::{qualified}", statement)
            self._precollected_node_ids[id(statement)] = node_id
            symbols[statement.name] = node_id
            return
        if isinstance(statement, ast.ClassDef):
            return

        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt | ast.ExceptHandler | ast.match_case):
                self._collect_callable_symbol_from_statement(child, parent_qualified, symbols)

    def _node_id_for(self, node: ast.FunctionDef | ast.AsyncFunctionDef, qualified: str) -> str:
        precollected_id = self._precollected_node_ids.get(id(node))
        if precollected_id is not None:
            return precollected_id
        return self._unique_symbol_node_id(f"{self.file_path}::{qualified}", node)

    def _collect_module_shadowed_names(self, body: list[ast.stmt]) -> set[str]:
        names: set[str] = set()
        for statement in body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names.update(self._collect_statement_binding_names(statement))
        return names

    def _collect_local_shadowed_names(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> set[str]:
        names = self._collect_argument_names(node.args)
        for statement in node.body:
            names.update(self._collect_statement_binding_names(statement))
        return names

    def _collect_argument_names(self, arguments: ast.arguments) -> set[str]:
        names = {arg.arg for arg in arguments.posonlyargs}
        names.update(arg.arg for arg in arguments.args)
        names.update(arg.arg for arg in arguments.kwonlyargs)
        if arguments.vararg is not None:
            names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            names.add(arguments.kwarg.arg)
        return names

    def _collect_direct_module_binding_names(self, statement: ast.AST) -> set[str]:
        names = self._collect_direct_statement_binding_names(statement)
        names.difference_update(self._collect_direct_module_import_names(statement))
        return names

    def _collect_direct_module_import_names(self, statement: ast.AST) -> set[str]:
        if isinstance(statement, ast.Import):
            return {alias.asname or alias.name.split(".", 1)[0] for alias in statement.names}
        if isinstance(statement, ast.ImportFrom):
            return {alias.asname or alias.name for alias in statement.names if alias.name != "*"}
        return set()

    def _collect_direct_statement_binding_names(self, statement: ast.AST) -> set[str]:
        names: set[str] = set()
        if isinstance(statement, ast.Assign):
            names.update(self._collect_target_names(*statement.targets))
        elif isinstance(statement, ast.AnnAssign | ast.AugAssign):
            names.update(self._collect_target_names(statement.target))
        elif isinstance(statement, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in statement.names if alias.name != "*")
        elif isinstance(statement, ast.For | ast.AsyncFor):
            names.update(self._collect_target_names(statement.target))
        elif isinstance(statement, ast.With | ast.AsyncWith):
            for item in statement.items:
                if item.optional_vars is not None:
                    names.update(self._collect_target_names(item.optional_vars))
        elif isinstance(statement, ast.ExceptHandler) and statement.name is not None:
            names.add(statement.name)
        elif isinstance(statement, ast.match_case):
            names.update(self._collect_pattern_binding_names(statement.pattern))
        return names

    def _collect_statement_binding_names(self, statement: ast.AST) -> set[str]:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            return set()
        if isinstance(statement, ast.ClassDef):
            return {statement.name}

        names: set[str] = set()
        if isinstance(statement, ast.Assign):
            names.update(self._collect_target_names(*statement.targets))
        elif isinstance(statement, ast.AnnAssign | ast.AugAssign):
            names.update(self._collect_target_names(statement.target))
        elif isinstance(statement, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in statement.names)
        elif isinstance(statement, ast.For | ast.AsyncFor):
            names.update(self._collect_target_names(statement.target))
        elif isinstance(statement, ast.With | ast.AsyncWith):
            for item in statement.items:
                if item.optional_vars is not None:
                    names.update(self._collect_target_names(item.optional_vars))
        elif isinstance(statement, ast.ExceptHandler) and statement.name is not None:
            names.add(statement.name)
        elif isinstance(statement, ast.match_case):
            names.update(self._collect_pattern_binding_names(statement.pattern))

        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt | ast.ExceptHandler | ast.match_case):
                names.update(self._collect_statement_binding_names(child))
            elif isinstance(child, ast.expr):
                names.update(self._collect_expression_binding_names(child))
        return names

    def _collect_pattern_binding_names(self, pattern: ast.pattern) -> set[str]:
        names: set[str] = set()
        if isinstance(pattern, ast.MatchAs) and pattern.name is not None and pattern.name != "_":
            names.add(pattern.name)
        elif isinstance(pattern, ast.MatchStar) and pattern.name is not None and pattern.name != "_":
            names.add(pattern.name)
        elif isinstance(pattern, ast.MatchMapping) and pattern.rest is not None and pattern.rest != "_":
            names.add(pattern.rest)

        for child in ast.iter_child_nodes(pattern):
            if isinstance(child, ast.pattern):
                names.update(self._collect_pattern_binding_names(child))
        return names

    def _collect_expression_binding_names(self, expression: ast.expr) -> set[str]:
        if isinstance(expression, ast.Lambda):
            return set()

        names: set[str] = set()
        if isinstance(expression, ast.NamedExpr):
            names.update(self._collect_target_names(expression.target))

        for child in ast.iter_child_nodes(expression):
            if isinstance(child, ast.expr):
                names.update(self._collect_expression_binding_names(child))
        return names

    def _collect_target_names(self, *targets: ast.expr) -> set[str]:
        names: set[str] = set()
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Tuple | ast.List):
                names.update(self._collect_target_names(*target.elts))
        return names

    def _resolve_callable(self, call_name: str) -> str | None:
        for scope in reversed(self.callable_scope_stack):
            if not scope.bare_call_visible:
                continue
            if call_name in scope.shadowed_names:
                return None
            target_id = scope.symbols.get(call_name)
            if target_id is not None:
                return target_id
        return None

    def _is_resolvable_unresolved_reference(self, call_name: str) -> bool:
        if "." in call_name:
            return False
        for scope in reversed(self.callable_scope_stack):
            if call_name in scope.shadowed_names:
                return False
            if not scope.bare_call_visible and call_name in scope.symbols:
                return False
        return True

    def _unique_symbol_node_id(self, base_id: str, node: ast.AST) -> str:
        if base_id not in self._symbol_node_ids:
            self._symbol_node_ids.add(base_id)
            return base_id

        duplicate_id = f"{base_id}@{getattr(node, 'lineno', 0)}:{getattr(node, 'col_offset', 0)}"
        self._symbol_node_ids.add(duplicate_id)
        return duplicate_id

    def _add_import_node(self, name: str, start_line: int, end_line: int, col: int) -> None:
        parent_id, _, _, _ = self.scope_stack[-1]
        qualified = f"{self.module_name}.{name}" if self.module_name else name
        self._import_occurrence += 1
        node_id = f"{self.file_path}::{qualified}@import:{start_line}:{col}:{self._import_occurrence}"
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
        prefix = "async def" if is_async else "def"
        signature = f"{prefix} {node.name}({self._format_arguments(node.args)})"
        if node.returns is not None:
            signature += f" -> {ast.unparse(node.returns)}"
        return signature

    def _format_arguments(self, arguments: ast.arguments) -> str:
        parts: list[str] = []
        positional = [*arguments.posonlyargs, *arguments.args]
        defaults = [None] * (len(positional) - len(arguments.defaults)) + list(arguments.defaults)

        for index, (arg, default) in enumerate(zip(positional, defaults)):
            parts.append(self._format_arg(arg, default))
            if index == len(arguments.posonlyargs) - 1:
                parts.append("/")

        if arguments.vararg is not None:
            parts.append(f"*{self._format_arg(arguments.vararg)}")
        elif arguments.kwonlyargs:
            parts.append("*")

        for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
            parts.append(self._format_arg(arg, default))

        if arguments.kwarg is not None:
            parts.append(f"**{self._format_arg(arguments.kwarg)}")

        return ", ".join(parts)

    def _format_arg(self, arg: ast.arg, default: ast.expr | None = None) -> str:
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}"
        return text

    def _call_name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base_name = self._call_name(node.value)
            if base_name:
                return f"{base_name}.{node.attr}"
        return ""

    def _qualify(self, parent_qualified: str, name: str) -> str:
        if not parent_qualified:
            return name
        return f"{parent_qualified}.{name}"
