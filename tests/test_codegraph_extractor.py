from __future__ import annotations

from lazycode.codegraph.extractor import extract_python_graph


def test_extracts_module_class_method_and_function() -> None:
    source = """\
import os

class Service:
    def run(self, name: str) -> str:
        return name.upper()

def helper() -> None:
    pass
"""

    result = extract_python_graph("pkg/service.py", source)

    names = {(n.kind, n.qualified_name) for n in result.nodes}
    assert ("module", "pkg.service") in names
    assert ("import", "pkg.service.os") in names
    assert ("class", "Service") in names
    assert ("method", "Service.run") in names
    assert ("function", "helper") in names

    contains = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "contains"}
    assert ("pkg/service.py::module", "pkg/service.py::Service", "contains") in contains
    assert ("pkg/service.py::Service", "pkg/service.py::Service.run", "contains") in contains


def test_extracts_async_function_signature() -> None:
    source = """\
async def fetch(url: str) -> bytes:
    return b""
"""

    result = extract_python_graph("pkg/http.py", source)

    node = next(n for n in result.nodes if n.name == "fetch")
    assert node.kind == "function"
    assert node.signature == "async def fetch(url: str) -> bytes"
    assert node.start_line == 1
    assert node.end_line == 2


def test_repeated_import_aliases_have_unique_node_ids() -> None:
    source = """\
import os as tools


def outer() -> None:
    import sys as tools

    if True:
        import json as tools
"""

    result = extract_python_graph("pkg/imports.py", source)

    import_nodes = [node for node in result.nodes if node.kind == "import"]
    assert len(import_nodes) == 3
    assert len({node.id for node in import_nodes}) == len(import_nodes)
    assert {node.qualified_name for node in import_nodes} == {"pkg.imports.tools"}


def test_repeated_top_level_functions_have_unique_node_ids() -> None:
    source = """\
def configure():
    return "first"


def configure():
    return "second"
"""

    result = extract_python_graph("pkg/settings.py", source)

    assert len({node.id for node in result.nodes}) == len(result.nodes)
    function_nodes = [
        node
        for node in result.nodes
        if node.kind == "function" and node.qualified_name == "configure"
    ]
    assert len(function_nodes) == 2
    assert [node.id for node in function_nodes] == [
        "pkg/settings.py::configure",
        "pkg/settings.py::configure@5:0",
    ]


def test_repeated_methods_have_unique_node_ids_and_contains_edges() -> None:
    source = """\
class Service:
    def run(self):
        return "first"

    def run(self):
        return "second"
"""

    result = extract_python_graph("pkg/service.py", source)

    assert len({node.id for node in result.nodes}) == len(result.nodes)
    method_nodes = [
        node
        for node in result.nodes
        if node.kind == "method" and node.qualified_name == "Service.run"
    ]
    assert len(method_nodes) == 2
    assert [node.id for node in method_nodes] == [
        "pkg/service.py::Service.run",
        "pkg/service.py::Service.run@5:4",
    ]

    contains_targets = {edge.target for edge in result.edges if edge.kind == "contains"}
    assert {node.id for node in method_nodes} <= contains_targets


def test_function_signature_includes_defaults_varargs_kwonly_and_kwargs() -> None:
    source = """\
def configure(path: str = "x", *items, enabled: bool = True, **opts) -> None:
    pass
"""

    result = extract_python_graph("pkg/config.py", source)

    node = next(n for n in result.nodes if n.name == "configure")
    assert node.signature.startswith("def configure(")
    assert "path: str = 'x'" in node.signature or 'path: str = "x"' in node.signature
    assert "*items" in node.signature
    assert "enabled: bool = True" in node.signature
    assert "**opts" in node.signature
    assert node.signature.endswith("-> None")
