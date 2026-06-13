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


def test_extracts_direct_call_edges_inside_file() -> None:
    source = """\
def helper() -> None:
    pass

def run() -> None:
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") in calls


def test_resolves_forward_local_function_call() -> None:
    source = """\
def run() -> None:
    helper()


def helper() -> None:
    pass
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") in calls
    assert not [ref for ref in result.unresolved_refs if ref.source == "pkg/service.py::run" and ref.name == "helper"]


def test_conditional_module_function_does_not_resolve_as_guaranteed_binding() -> None:
    source = """\
if flag:
    def helper() -> None:
        pass


def run() -> None:
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    assert not [
        ref
        for ref in result.unresolved_refs
        if ref.source == "pkg/service.py::run" and ref.name == "helper" and ref.is_resolvable
    ]


def test_nested_function_does_not_leak_to_unrelated_function() -> None:
    source = """\
def outer() -> None:
    def inner() -> None:
        pass


def run() -> None:
    inner()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::outer.inner", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "inner", "calls") in unresolved


def test_nested_function_call_before_definition_does_not_resolve() -> None:
    source = """\
def outer() -> None:
    inner()

    def inner() -> None:
        pass
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::outer", "pkg/service.py::outer.inner", "calls") not in calls
    refs = [
        ref
        for ref in result.unresolved_refs
        if ref.source == "pkg/service.py::outer" and ref.name == "inner"
    ]
    assert refs
    assert all(not ref.is_resolvable for ref in refs)


def test_nested_function_call_after_definition_resolves() -> None:
    source = """\
def outer() -> None:
    def inner() -> None:
        pass

    inner()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::outer", "pkg/service.py::outer.inner", "calls") in calls
    assert not [
        ref
        for ref in result.unresolved_refs
        if ref.source == "pkg/service.py::outer" and ref.name == "inner"
    ]


def test_nested_function_rebind_shadows_callable_resolution() -> None:
    source = """\
def shadowed() -> None:
    def helper() -> None:
        pass

    helper = get_helper
    helper()


def unshadowed() -> None:
    def helper() -> None:
        pass

    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::shadowed", "pkg/service.py::shadowed.helper", "calls") not in calls
    assert ("pkg/service.py::unshadowed", "pkg/service.py::unshadowed.helper", "calls") in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::shadowed", "helper", "calls") in unresolved


def test_definition_time_calls_do_not_create_body_call_edges() -> None:
    source = """\
def helper() -> None:
    pass


def decorate(fn):
    return fn


@decorate(helper())
def run(x=helper()) -> None:
    pass
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    assert ("pkg/service.py::run", "pkg/service.py::decorate", "calls") not in calls


def test_bare_sibling_method_call_is_unresolved() -> None:
    source = """\
class Service:
    def helper(self) -> None:
        pass

    def run(self) -> None:
        helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::Service.run", "pkg/service.py::Service.helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::Service.run", "helper", "calls") in unresolved


def test_parameter_shadowing_prevents_outer_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


def run(helper) -> None:
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_lambda_parameter_shadowing_prevents_outer_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


def run() -> None:
    cb = lambda helper: helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    refs = [
        ref
        for ref in result.unresolved_refs
        if ref.source == "pkg/service.py::run" and ref.name == "helper"
    ]
    assert refs
    assert all(not ref.is_resolvable for ref in refs)


def test_comprehension_target_shadowing_prevents_outer_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


def run(items):
    return [helper() for helper in items]
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    refs = [
        ref
        for ref in result.unresolved_refs
        if ref.source == "pkg/service.py::run" and ref.name == "helper"
    ]
    assert refs
    assert all(not ref.is_resolvable for ref in refs)


def test_assignment_shadowing_prevents_outer_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


def get_helper():
    return helper


def run() -> None:
    helper = get_helper
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_nested_assignment_shadowing_prevents_outer_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


def run(flag) -> None:
    if flag:
        helper = lambda: None
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_nested_function_resolution_wins_over_local_shadow_blockers() -> None:
    source = """\
def helper() -> None:
    pass


def run() -> None:
    def helper() -> None:
        pass

    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::run.helper", "calls") in calls
    assert not [
        ref
        for ref in result.unresolved_refs
        if ref.source == "pkg/service.py::run" and ref.name == "helper"
    ]


def test_import_shadowing_prevents_outer_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


def run() -> None:
    import helper
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_for_loop_target_shadowing_prevents_outer_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


def run_loop(items) -> None:
    for helper in items:
        pass
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run_loop", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run_loop", "helper", "calls") in unresolved


def test_local_function_defined_inside_if_does_not_resolve_later_bare_call() -> None:
    source = """\
def run(flag) -> None:
    if flag:
        def helper() -> None:
            pass
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::run.helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_class_definition_shadows_same_named_function_for_later_call() -> None:
    source = """\
def helper() -> None:
    pass


class helper:
    pass


def run() -> None:
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_module_assignment_shadows_same_named_function_for_later_function_body_call() -> None:
    source = """\
def helper() -> None:
    pass


helper = factory()


def run() -> None:
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_later_module_function_overrides_earlier_same_named_assignment_for_call_resolution() -> None:
    source = """\
helper = factory()


def helper() -> None:
    pass


def run() -> None:
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") in calls
    assert not [
        ref
        for ref in result.unresolved_refs
        if ref.source == "pkg/service.py::run" and ref.name == "helper"
    ]


def test_star_import_after_same_named_function_prevents_later_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


from other import *


def run() -> None:
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_local_function_defined_inside_match_case_does_not_resolve_later_bare_call() -> None:
    source = """\
def run(value) -> None:
    match value:
        case 1:
            def helper() -> None:
                pass
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::run.helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_match_case_capture_shadowing_prevents_outer_call_resolution() -> None:
    source = """\
def helper() -> None:
    pass


def run(value) -> None:
    match value:
        case helper:
            pass
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_nested_walrus_assignment_shadows_later_call() -> None:
    source = """\
def helper() -> None:
    pass


def run() -> None:
    if (helper := get_helper()):
        pass
    helper()
"""

    result = extract_python_graph("pkg/service.py", source)

    calls = {(e.source, e.target, e.kind) for e in result.edges if e.kind == "calls"}
    assert ("pkg/service.py::run", "pkg/service.py::helper", "calls") not in calls
    unresolved = {(ref.source, ref.name, ref.kind) for ref in result.unresolved_refs}
    assert ("pkg/service.py::run", "helper", "calls") in unresolved


def test_records_unresolved_attribute_call() -> None:
    source = """\
def run(client):
    client.send()
"""

    result = extract_python_graph("pkg/service.py", source)

    assert result.unresolved_refs[0].source == "pkg/service.py::run"
    assert result.unresolved_refs[0].name == "client.send"
    assert result.unresolved_refs[0].kind == "calls"
