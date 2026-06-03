"""Org-grouping extractor tests (queues, public groups, roles).

Only GroupingExtractor is registered, so edges to kinds owned by other
extractors (e.g. `object`) resolve through the default stub resolvers; edges in
this extractor's own world are asserted straight off `extract()`.
"""
from __future__ import annotations

from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.groups import GroupingExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _build(tmp_path: Path):
    return (
        GraphBuilder()
        .register(GroupingExtractor())
        .register_resolver(*resolvers.default_resolvers())
        .build(tmp_path)
    )


def _by_id(result):
    return {n["id"]: n for n in result["nodes"]}


# fixture writers
def _write_queue(d: Path, name: str, sobjects, members=()):
    sobj_xml = "".join(
        f"<queueSobject><sobjectType>{s}</sobjectType></queueSobject>" for s in sobjects
    )
    mem_xml = "".join(f"<members><groups>{m}</groups></members>" for m in members)
    (d / f"{name}.queue-meta.xml").write_text(
        f'<?xml version="1.0"?><Queue {NS}>'
        f"<name>{name}</name>{sobj_xml}{mem_xml}</Queue>"
    )


def _write_group(d: Path, name: str, group_members=(), user_members=()):
    g = "".join(f"<members><groups>{m}</groups></members>" for m in group_members)
    u = "".join(f"<members><users>{m}</users></members>" for m in user_members)
    (d / f"{name}.group-meta.xml").write_text(
        f'<?xml version="1.0"?><Group {NS}>'
        f"<name>{name}</name>{g}{u}</Group>"
    )


def _write_role(d: Path, name: str, parent=None):
    parent_xml = f"<parentRole>{parent}</parentRole>" if parent else ""
    (d / f"{name}.role-meta.xml").write_text(
        f'<?xml version="1.0"?><Role {NS}>'
        f"<name>{name}</name>{parent_xml}</Role>"
    )


def test_handles_only_grouping_files():
    ex = GroupingExtractor()
    assert ex.handles(Path("Acme_Cases.queue-meta.xml"))
    assert ex.handles(Path("Acme_Team.group-meta.xml"))
    assert ex.handles(Path("Acme_Manager.role-meta.xml"))
    assert not ex.handles(Path("Acme.object-meta.xml"))
    assert not ex.handles(Path("AcmeTrigger.trigger"))


# queue
def test_queue_node_and_on_edges(tmp_path):
    _write_queue(tmp_path, "Acme_Cases", ["Case", "MeterPoint__c"], members=["Acme_Agents"])
    ex = GroupingExtractor()
    nodes, edges = ex.extract(tmp_path / "Acme_Cases.queue-meta.xml")

    assert nodes[0]["id"] == "queue/Acme_Cases"
    assert nodes[0]["type"] == "queue"
    assert nodes[0]["members"] == ["Acme_Agents"]
    assert nodes[0]["sobjects"] == ["Case", "MeterPoint__c"]

    on = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("on", "object", "Case") in on
    assert ("on", "object", "MeterPoint__c") in on


def test_queue_on_edge_resolves_to_object(tmp_path):
    _write_queue(tmp_path, "Acme_Cases", ["Globex__c"])
    result = _build(tmp_path)
    nodes = _by_id(result)
    assert "queue/Acme_Cases" in nodes
    # `object` kind is owned by another extractor; in isolation it resolves to a stub.
    obj_edges = [e for e in result["edges"] if e["type"] == "on"]
    assert any(e["src"] == "queue/Acme_Cases" and e["dst"] == "object/Globex__c"
               for e in obj_edges)
    assert nodes["object/Globex__c"].get("external") is True


def test_queue_without_sobject_has_no_edges(tmp_path):
    _write_queue(tmp_path, "Acme_Plain", [])
    ex = GroupingExtractor()
    nodes, edges = ex.extract(tmp_path / "Acme_Plain.queue-meta.xml")
    assert nodes[0]["id"] == "queue/Acme_Plain"
    assert "sobjects" not in nodes[0]
    assert edges == []


# public group
def test_public_group_node_and_members(tmp_path):
    _write_group(tmp_path, "Acme_Team", group_members=["Globex_Sub"], user_members=["jdoe"])
    ex = GroupingExtractor()
    nodes, edges = ex.extract(tmp_path / "Acme_Team.group-meta.xml")

    assert nodes[0]["id"] == "publicgroup/Acme_Team"
    assert nodes[0]["type"] == "publicgroup"
    # all member developerNames are still kept as a flat attr
    assert set(nodes[0]["members"]) == {"Globex_Sub", "jdoe"}
    # nested public-group members become `contains` edges; users stay attr-only
    triples = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("contains", "publicgroup", "Globex_Sub") in triples
    assert not any(t[2] == "jdoe" for t in triples)


def test_group_member_roles_and_queues_contained(tmp_path):
    """roles / roleAndSubordinates / queues members -> contains edges."""
    (tmp_path / "Acme_Mixed.group-meta.xml").write_text(
        f'<?xml version="1.0"?><Group {NS}><name>Acme_Mixed</name>'
        "<members><roles>Acme_Manager</roles></members>"
        "<members><roleAndSubordinates>Acme_VP</roleAndSubordinates></members>"
        "<members><queues>Acme_Cases</queues></members></Group>"
    )
    _, edges = GroupingExtractor().extract(tmp_path / "Acme_Mixed.group-meta.xml")
    triples = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("contains", "role", "Acme_Manager") in triples
    assert ("contains", "role", "Acme_VP") in triples
    assert ("contains", "queue", "Acme_Cases") in triples


# role
def test_role_parent_contains_edge(tmp_path):
    _write_role(tmp_path, "Acme_VP")
    _write_role(tmp_path, "Acme_Manager", parent="Acme_VP")
    ex = GroupingExtractor()
    nodes, edges = ex.extract(tmp_path / "Acme_Manager.role-meta.xml")

    assert nodes[0]["id"] == "role/Acme_Manager"
    assert nodes[0]["parent"] == "Acme_VP"
    # contains edge goes parent -> this role
    assert len(edges) == 1
    e = edges[0]
    assert e["src"] == "role/Acme_VP"
    assert e["type"] == "contains"
    assert e["to_kind"] == "role"
    assert e["to_name"] == "Acme_Manager"


def test_root_role_has_no_parent_edge(tmp_path):
    _write_role(tmp_path, "Acme_CEO")
    ex = GroupingExtractor()
    nodes, edges = ex.extract(tmp_path / "Acme_CEO.role-meta.xml")
    assert nodes[0]["id"] == "role/Acme_CEO"
    assert "parent" not in nodes[0]
    assert edges == []


def test_role_contains_resolves_in_isolation(tmp_path):
    # `role` is a default stub kind, and Acme_Manager exists in-repo, so the
    # parent role's `contains` edge resolves to the real child role node.
    _write_role(tmp_path, "Acme_VP")
    _write_role(tmp_path, "Acme_Manager", parent="Acme_VP")
    result = _build(tmp_path)
    nodes = _by_id(result)
    assert "role/Acme_VP" in nodes and "role/Acme_Manager" in nodes
    edges = {(e["src"], e["type"], e["dst"]) for e in result["edges"]}
    assert ("role/Acme_VP", "contains", "role/Acme_Manager") in edges
    assert [u for u in result["unresolved"] if u["to_kind"] == "role"] == []
    assert result["errors"] == []


# robustness
def test_broken_xml_still_emits_node(tmp_path):
    (tmp_path / "Acme_Bad.queue-meta.xml").write_text("<Queue><name>oops")  # malformed
    ex = GroupingExtractor()
    nodes, edges = ex.extract(tmp_path / "Acme_Bad.queue-meta.xml")
    assert nodes[0]["id"] == "queue/Acme_Bad"
    assert edges == []


def test_build_does_not_raise_and_separates_kinds(tmp_path):
    _write_queue(tmp_path, "Acme_Cases", ["Case"])
    _write_group(tmp_path, "Acme_Team", user_members=["jdoe"])
    _write_role(tmp_path, "Acme_Manager", parent="Acme_VP")
    result = _build(tmp_path)
    kinds = {n["type"] for n in result["nodes"] if not n.get("external")}
    assert {"queue", "publicgroup", "role"} <= kinds
    assert result["errors"] == []
