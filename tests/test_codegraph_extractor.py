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
