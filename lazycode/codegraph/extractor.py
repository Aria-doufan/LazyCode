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
    unsafe_unknown_names: bool = False
    import_bindings: dict[str, tuple[str, str]] = field(default_factory=dict)


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
        (
            module_symbols,
            module_shadowed_names,
            module_has_star_import,
            module_import_bindings,
        ) = self._collect_module_resolution_state(tree.body)
        self.callable_scope_stack.append(
            _CallableScope(
                module_symbols,
                True,
                module_shadowed_names,
                module_has_star_import,
                module_import_bindings,
            )
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

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._clear_local_import_bindings_for_names(
            self._collect_expression_binding_names(node.test)
        )
        self._visit_isolated_statement_sequence(node.body)
        self._visit_isolated_statement_sequence(node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self.visit(node.target)
        self._clear_local_import_bindings_for_names(self._collect_target_names(node.target))
        self._visit_loop_body_then_orelse(node.body, node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.iter)
        self.visit(node.target)
        self._clear_local_import_bindings_for_names(self._collect_target_names(node.target))
        self._visit_loop_body_then_orelse(node.body, node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._clear_local_import_bindings_for_names(
            self._collect_expression_binding_names(node.test)
        )
        self._visit_loop_body_then_orelse(node.body, node.orelse)

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_isolated_statement_sequence(node.body)
        for handler in node.handlers:
            self._visit_isolated_node(handler)
        self._visit_isolated_statement_sequence(node.orelse)
        self._visit_isolated_statement_sequence(node.finalbody)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name is not None:
            self._clear_local_import_bindings_for_names({node.name})
        self._visit_statement_sequence_with_temporary_callables(node.body)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        for case in node.cases:
            self._visit_isolated_node(case)

    def visit_match_case(self, node: ast.match_case) -> None:
        self._clear_local_import_bindings_for_names(
            self._collect_pattern_binding_names(node.pattern)
        )
        if node.guard is not None:
            self.visit(node.guard)
        self._visit_statement_sequence_with_temporary_callables(node.body)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        bound_names: set[str] = set()
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
                bound_names.update(self._collect_target_names(item.optional_vars))
        self._clear_local_import_bindings_for_names(bound_names)
        self._visit_statement_sequence(node.body)

    def _visit_statement_sequence(self, body: list[ast.stmt]) -> None:
        for statement in body:
            if self._in_callable_scope():
                self._apply_local_from_import_bindings_before_visit(statement)
            self.visit(statement)
            if self._in_callable_scope():
                self._clear_local_import_bindings_after_visit(statement)

    def _visit_statement_sequence_with_temporary_callables(
        self, body: list[ast.stmt]
    ) -> None:
        if not self._in_callable_scope():
            self._visit_statement_sequence(body)
            return
        scope = self.callable_scope_stack[-1]
        symbols = dict(scope.symbols)
        _, parent_qualified, _, _ = self.scope_stack[-1]
        scope.symbols.update(self._collect_callable_symbols(body, parent_qualified))
        try:
            self._visit_statement_sequence(body)
        finally:
            scope.symbols = symbols

    def _visit_isolated_statement_sequence(self, body: list[ast.stmt]) -> None:
        if not self._in_callable_scope():
            self._visit_statement_sequence(body)
            return
        scope = self.callable_scope_stack[-1]
        import_bindings = dict(scope.import_bindings)
        try:
            self._visit_statement_sequence_with_temporary_callables(body)
        finally:
            scope.import_bindings = import_bindings

    def _visit_loop_body_then_orelse(
        self, body: list[ast.stmt], orelse: list[ast.stmt]
    ) -> None:
        if not self._in_callable_scope():
            self._visit_statement_sequence(body)
            self._visit_statement_sequence(orelse)
            return
        scope = self.callable_scope_stack[-1]
        import_bindings = dict(scope.import_bindings)
        try:
            self._visit_statement_sequence_with_temporary_callables(body)
        finally:
            scope.import_bindings = import_bindings
        self._visit_statement_sequence_with_temporary_callables(orelse)

    def _visit_isolated_node(self, node: ast.AST) -> None:
        if not self._in_callable_scope():
            self.visit(node)
            return
        scope = self.callable_scope_stack[-1]
        import_bindings = dict(scope.import_bindings)
        try:
            self.visit(node)
        finally:
            scope.import_bindings = import_bindings

    def _in_callable_scope(self) -> bool:
        return bool(self.scope_stack and self.scope_stack[-1][3])

    def _clear_local_import_bindings_for_names(self, names: set[str]) -> None:
        if not self._in_callable_scope():
            return
        for name in names:
            self.callable_scope_stack[-1].import_bindings.pop(name, None)

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
            self._apply_local_from_import_bindings_before_visit(statement)
            self.visit(statement)
            self._clear_local_import_bindings_after_visit(statement)
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
                import_module, import_name = self._resolve_import_binding(call_name)
                self.result.unresolved_refs.append(
                    UnresolvedReference(
                        source=current_id,
                        name=call_name,
                        kind="calls",
                        line=getattr(node, "lineno", 0),
                        col=getattr(node, "col_offset", 0),
                        file_path=self.file_path,
                        is_resolvable=self._is_resolvable_unresolved_reference(call_name),
                        import_module=import_module,
                        import_name=import_name,
                    )
                )
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.callable_scope_stack.append(
            _CallableScope({}, True, self._collect_argument_names(node.args))
        )
        self.generic_visit(node)
        self.callable_scope_stack.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node)

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp
    ) -> None:
        shadowed_names: set[str] = set()
        for generator in node.generators:
            shadowed_names.update(self._collect_target_names(generator.target))
        self.callable_scope_stack.append(_CallableScope({}, True, shadowed_names))
        self.generic_visit(node)
        self.callable_scope_stack.pop()

    def _collect_callable_symbols(self, body: list[ast.stmt], parent_qualified: str) -> dict[str, str]:
        symbols: dict[str, str] = {}
        for statement in body:
            self._collect_callable_symbol_from_statement(statement, parent_qualified, symbols)
        return symbols

    def _collect_module_resolution_state(
        self, body: list[ast.stmt]
    ) -> tuple[dict[str, str], set[str], bool, dict[str, tuple[str, str]]]:
        final_bindings: dict[str, str | None] = {}
        unsafe_bindings: dict[str, bool] = {}
        import_bindings: dict[str, tuple[str, str]] = {}
        has_star_import = False
        for statement in body:
            if self._collect_module_binding_from_statement(
                statement, final_bindings, unsafe_bindings, import_bindings, True
            ):
                has_star_import = True
        symbols = {
            name: node_id for name, node_id in final_bindings.items() if node_id is not None
        }
        shadowed_names = {
            name
            for name, node_id in final_bindings.items()
            if node_id is None and unsafe_bindings.get(name, False)
        }
        return symbols, shadowed_names, has_star_import, import_bindings

    def _collect_module_binding_from_statement(
        self,
        statement: ast.AST,
        final_bindings: dict[str, str | None],
        unsafe_bindings: dict[str, bool],
        import_bindings: dict[str, tuple[str, str]],
        allow_import_binding: bool,
    ) -> bool:
        has_star_import = False
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            qualified = self._qualify("", statement.name)
            node_id = self._unique_symbol_node_id(f"{self.file_path}::{qualified}", statement)
            self._precollected_node_ids[id(statement)] = node_id
            final_bindings[statement.name] = node_id
            unsafe_bindings[statement.name] = False
            import_bindings.pop(statement.name, None)
            return has_star_import
        if isinstance(statement, ast.ClassDef):
            final_bindings[statement.name] = None
            unsafe_bindings[statement.name] = True
            import_bindings.pop(statement.name, None)
            return has_star_import
        if isinstance(statement, ast.ImportFrom) and any(alias.name == "*" for alias in statement.names):
            for name in list(final_bindings):
                final_bindings[name] = None
                unsafe_bindings[name] = True
            import_bindings.clear()
            return True

        for name in self._collect_direct_module_binding_names(statement):
            final_bindings[name] = None
            unsafe_bindings[name] = True
            import_bindings.pop(name, None)
        for name in self._collect_direct_module_import_names(statement):
            final_bindings[name] = None
            unsafe_bindings[name] = True
            import_bindings.pop(name, None)
        if allow_import_binding:
            import_bindings.update(self._collect_direct_from_import_bindings(statement))

        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt | ast.ExceptHandler | ast.match_case):
                if self._collect_module_binding_from_statement(
                    child, final_bindings, unsafe_bindings, import_bindings, False
                ):
                    has_star_import = True
        return has_star_import

    def _collect_callable_symbol_from_statement(
        self, statement: ast.AST, parent_qualified: str, symbols: dict[str, str]
    ) -> None:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            qualified = self._qualify(parent_qualified, statement.name)
            node_id = self._unique_symbol_node_id(f"{self.file_path}::{qualified}", statement)
            self._precollected_node_ids[id(statement)] = node_id
            symbols[statement.name] = node_id

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

    def _collect_direct_from_import_bindings(
        self, statement: ast.AST
    ) -> dict[str, tuple[str, str]]:
        if not isinstance(statement, ast.ImportFrom):
            return {}
        module = self._absolute_import_module(statement)
        if not module:
            return {}
        return {
            alias.asname or alias.name: (module, alias.name)
            for alias in statement.names
            if alias.name != "*"
        }

    def _apply_local_from_import_bindings_before_visit(self, statement: ast.stmt) -> None:
        if not isinstance(statement, ast.ImportFrom):
            return
        self.callable_scope_stack[-1].import_bindings.update(
            self._collect_direct_from_import_bindings(statement)
        )

    def _clear_local_import_bindings_after_visit(self, statement: ast.stmt) -> None:
        if isinstance(statement, ast.ImportFrom):
            return
        bound_names = self._collect_statement_binding_names(statement)
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound_names.add(statement.name)
        for name in bound_names:
            self.callable_scope_stack[-1].import_bindings.pop(name, None)

    def _absolute_import_module(self, statement: ast.ImportFrom) -> str:
        if statement.level == 0:
            return statement.module or ""
        module_parts = self.module_name.split(".") if self.module_name else []
        path = PurePosixPath(self.file_path.replace("\\", "/"))
        if path.stem == "__init__":
            package_parts = module_parts
        else:
            package_parts = module_parts[:-1]
        if statement.level > 1:
            package_parts = package_parts[: -(statement.level - 1)]
        if statement.module:
            package_parts.extend(statement.module.split("."))
        return ".".join(part for part in package_parts if part)

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
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(child.name)
            elif isinstance(child, ast.stmt | ast.ExceptHandler | ast.match_case):
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

    def _resolve_import_binding(self, call_name: str) -> tuple[str, str]:
        if "." in call_name:
            return "", ""
        if not self.callable_scope_stack:
            return "", ""

        current_scope = self.callable_scope_stack[-1]
        binding = current_scope.import_bindings.get(call_name)
        if binding is not None:
            return binding
        if call_name in current_scope.shadowed_names or call_name in current_scope.symbols:
            return "", ""

        module_scope = self.callable_scope_stack[0]
        for enclosing_scope in self.callable_scope_stack[1:-1]:
            if (
                call_name in enclosing_scope.import_bindings
                or call_name in enclosing_scope.shadowed_names
                or call_name in enclosing_scope.symbols
            ):
                return "", ""

        binding = module_scope.import_bindings.get(call_name)
        if binding is not None:
            return binding
        return "", ""

    def _is_resolvable_unresolved_reference(self, call_name: str) -> bool:
        if "." in call_name:
            return False
        for scope in reversed(self.callable_scope_stack):
            if call_name in scope.shadowed_names:
                return False
            if not scope.bare_call_visible and call_name in scope.symbols:
                return False
            if scope.unsafe_unknown_names and call_name not in scope.symbols:
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
