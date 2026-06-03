"""Trigger extractor tests."""
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.triggers import TriggerExtractor

EX = TriggerExtractor()


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


# Thin trigger delegating to a handler class. Exercises multiple events,
# `new Handler(`, `Handler.method(` delegation, a namespaced/inner-dotted handler,
# and noise that must be ignored (System.debug, Trigger context vars, control-flow
# keywords).
METER_TRIGGER = """trigger MeterPointTrigger on MeterPoint__c (before insert,
        after update, before delete) {

    System.debug('entering trigger');

    if (Trigger.isBefore) {
        AcmeMeterPointHandler.beforeWork(Trigger.new);
    } else {
        AcmeMeterPointHandler handler = new AcmeMeterPointHandler();
        handler.run(Trigger.oldMap);
        Acme.RoutingService.dispatch(Trigger.new);
    }

    new AcmeAuditLog(Trigger.operationType);
}
"""


def test_trigger_node_and_on_edge_preserved():
    """Trigger node plus the on -> object edge."""
    f = _w(Path("/tmp/_acme_trig/MeterPointTrigger.trigger"), METER_TRIGGER)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)

    assert "trigger/MeterPointTrigger" in ids
    n = ids["trigger/MeterPointTrigger"]
    assert n["type"] == "trigger"
    # the raw events string attr
    assert n["events"] == "before insert, after update, before delete"

    assert ("trigger/MeterPointTrigger", "on", "object", "MeterPoint__c") in _et(edges)


def test_event_list_attr():
    """Events split into a structured, normalized list attr."""
    f = _w(Path("/tmp/_acme_trig/MeterPointTrigger.trigger"), METER_TRIGGER)
    nodes, _ = EX.extract(f)
    n = _ids(nodes)["trigger/MeterPointTrigger"]
    assert n["event_list"] == ["before insert", "after update", "before delete"]


def test_dotted_call_emits_method_and_class():
    """`Handler.method(` -> calls -> both apexmethod/Handler.method and apexclass/Handler."""
    f = _w(Path("/tmp/_acme_trig/MeterPointTrigger.trigger"), METER_TRIGGER)
    _, edges = EX.extract(f)
    et = _et(edges)

    assert ("trigger/MeterPointTrigger", "calls", "apexmethod",
            "AcmeMeterPointHandler.beforeWork") in et
    assert ("trigger/MeterPointTrigger", "calls", "apexclass",
            "AcmeMeterPointHandler") in et

    # namespaced/inner-dotted head -> class is the LAST segment
    assert ("trigger/MeterPointTrigger", "calls", "apexmethod",
            "RoutingService.dispatch") in et
    assert ("trigger/MeterPointTrigger", "calls", "apexclass", "RoutingService") in et


def test_new_call_emits_class():
    """`new ClassName(` -> calls -> apexclass/ClassName."""
    f = _w(Path("/tmp/_acme_trig/MeterPointTrigger.trigger"), METER_TRIGGER)
    _, edges = EX.extract(f)
    et = _et(edges)

    assert ("trigger/MeterPointTrigger", "calls", "apexclass", "AcmeAuditLog") in et
    # `new AcmeMeterPointHandler()` also contributes the class (deduped with the
    # dotted-call class ref — emitted exactly once).
    assert ("trigger/MeterPointTrigger", "calls", "apexclass", "AcmeMeterPointHandler") in et


def test_noise_is_not_misread_as_a_call():
    """System.* / Trigger context vars / control-flow keywords are NOT classes."""
    f = _w(Path("/tmp/_acme_trig/MeterPointTrigger.trigger"), METER_TRIGGER)
    _, edges = EX.extract(f)
    call_targets = {e["to_name"] for e in edges if e["type"] == "calls"}

    assert "System" not in call_targets
    assert "Trigger" not in call_targets
    # `System.debug(` must not produce an apexmethod ref either
    assert "System.debug" not in call_targets
    assert "if" not in call_targets and "else" not in call_targets


def test_class_refs_emitted_once_each():
    """Dedup: each (class) and (class.method) target appears at most once."""
    f = _w(Path("/tmp/_acme_trig/MeterPointTrigger.trigger"), METER_TRIGGER)
    _, edges = EX.extract(f)
    calls = [(e["to_kind"], e["to_name"]) for e in edges if e["type"] == "calls"]
    assert len(calls) == len(set(calls))
    assert calls.count(("apexclass", "AcmeMeterPointHandler")) == 1


def test_handles():
    assert EX.handles(Path("x/MeterPointTrigger.trigger")) is True
    assert EX.handles(Path("x/MeterPointTrigger.trigger-meta.xml")) is False
    assert EX.handles(Path("x/AcmeFoo.cls")) is False


def test_never_raises_on_broken_source():
    for text in ("", "}}}{{{ not a trigger", "trigger Broken on  ( {",
                 "trigger T on A__c (before insert) { Handler.do( "):
        f = _w(Path("/tmp/_acme_trig/Broken.trigger"), text)
        nodes, edges = EX.extract(f)          # must not raise
        assert isinstance(nodes, list) and isinstance(edges, list)
        assert any(n["type"] == "trigger" for n in nodes)


def test_no_body_means_no_call_edges():
    """A trigger with an empty body emits the node + on edge, but no calls."""
    f = _w(Path("/tmp/_acme_trig/EmptyTrigger.trigger"),
           "trigger EmptyTrigger on Acme__c (before insert) {}\n")
    nodes, edges = EX.extract(f)
    et = _et(edges)
    assert ("trigger/EmptyTrigger", "on", "object", "Acme__c") in et
    assert not any(e["type"] == "calls" for e in edges)


def test_header_object_not_misread_as_call():
    """The `on Object(` header must not be parsed as a `Object(` call."""
    f = _w(Path("/tmp/_acme_trig/HeaderTrigger.trigger"),
           "trigger HeaderTrigger on MeterPoint__c (after insert) {\n"
           "    AcmeHandler.handle(Trigger.new);\n}\n")
    _, edges = EX.extract(f)
    call_targets = {e["to_name"] for e in edges if e["type"] == "calls"}
    # MeterPoint__c appears only via the `on` edge, never as a calls target
    assert "MeterPoint__c" not in call_targets
    assert ("trigger/HeaderTrigger", "calls", "apexmethod", "AcmeHandler.handle") in _et(edges)


def test_build_graph_resolves_and_stubs(tmp_path):
    """Graph build with the default resolvers: delegation targets resolve to
    external stubs (the handler classes aren't in this tree)."""
    fa = tmp_path / "force-app" / "main" / "default" / "triggers"
    _w(fa / "MeterPointTrigger.trigger", METER_TRIGGER)

    g = (GraphBuilder()
         .register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    assert g["errors"] == []
    ids = {n["id"]: n for n in g["nodes"]}

    assert "trigger/MeterPointTrigger" in ids
    assert ids["trigger/MeterPointTrigger"]["event_list"] == [
        "before insert", "after update", "before delete"]
    # delegation targets become external stubs (not in repo)
    assert ids["apexclass/AcmeMeterPointHandler"].get("external") is True
    assert ids["apexmethod/AcmeMeterPointHandler.beforeWork"].get("external") is True

    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert ("trigger/MeterPointTrigger", "on", "object/MeterPoint__c") in edges
    assert ("trigger/MeterPointTrigger", "calls",
            "apexclass/AcmeMeterPointHandler") in edges
    assert ("trigger/MeterPointTrigger", "calls",
            "apexmethod/AcmeMeterPointHandler.beforeWork") in edges
    assert ("trigger/MeterPointTrigger", "calls", "apexclass/AcmeAuditLog") in edges
