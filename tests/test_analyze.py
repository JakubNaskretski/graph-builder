"""Tests for graphbuilder.analyze, run against small hand-built graphs."""
from __future__ import annotations

from graphbuilder.analyze import (
    graph_summary,
    impact,
    orphans,
    permission_reachability,
)


def _n(nid, ntype, **attrs):
    return {"id": nid, "type": ntype, "label": nid.split("/", 1)[-1], **attrs}


def _e(src, etype, dst):
    return {"src": src, "type": etype, "dst": dst}


def _chain_graph():
    """trigger -> apexclass -> object, each depending on the next.

    In reverse: the object is depended on by the class and trigger; the class
    is depended on by the trigger.
    """
    return {
        "nodes": [
            _n("trigger/AcmeTrigger", "trigger"),
            _n("apexclass/AcmeHandler", "apexclass"),
            _n("object/MeterPoint", "object"),
        ],
        "edges": [
            _e("trigger/AcmeTrigger", "calls", "apexclass/AcmeHandler"),
            _e("apexclass/AcmeHandler", "references", "object/MeterPoint"),
        ],
        "unresolved": [],
        "errors": [],
    }


# --------------------------------------------------------------------------- impact
def test_impact_transitive_dependants_by_reverse_edges():
    g = _chain_graph()
    res = impact(g, "object/MeterPoint")
    by_id = {r["id"]: r["depth"] for r in res}
    assert by_id == {
        "apexclass/AcmeHandler": 1,
        "trigger/AcmeTrigger": 2,
    }


def test_impact_respects_max_depth():
    g = _chain_graph()
    res = impact(g, "object/MeterPoint", max_depth=1)
    assert [r["id"] for r in res] == ["apexclass/AcmeHandler"]


def test_impact_leaf_has_no_dependants():
    g = _chain_graph()
    assert impact(g, "trigger/AcmeTrigger") == []


def test_impact_unknown_node_returns_empty():
    g = _chain_graph()
    assert impact(g, "object/DoesNotExist") == []
    assert impact(g, None) == []


def test_impact_terminates_on_cycle():
    # A -> B -> A reference cycle; impact must terminate and not repeat A.
    g = {
        "nodes": [_n("apexclass/Globex", "apexclass"), _n("apexclass/Acme", "apexclass")],
        "edges": [
            _e("apexclass/Globex", "calls", "apexclass/Acme"),
            _e("apexclass/Acme", "calls", "apexclass/Globex"),
        ],
    }
    res = impact(g, "apexclass/Acme")
    ids = [r["id"] for r in res]
    # Globex depends on Acme (depth 1); Acme itself is not re-reported.
    assert ids == ["apexclass/Globex"]


# --------------------------------------------------------------------------- orphans
def test_orphans_excludes_nodes_with_incoming_and_externals():
    g = {
        "nodes": [
            _n("trigger/AcmeTrigger", "trigger"),          # no incoming -> orphan
            _n("apexclass/AcmeHandler", "apexclass"),       # has incoming
            _n("object/StandardThing", "object", external=True),  # external, excluded
            _n("flow/MeterPointFlow", "flow"),              # no incoming -> orphan
        ],
        "edges": [
            _e("trigger/AcmeTrigger", "calls", "apexclass/AcmeHandler"),
            _e("apexclass/AcmeHandler", "references", "object/StandardThing"),
        ],
    }
    assert orphans(g) == ["flow/MeterPointFlow", "trigger/AcmeTrigger"]


def test_orphans_type_filter_string_and_iterable():
    g = {
        "nodes": [
            _n("trigger/AcmeTrigger", "trigger"),
            _n("flow/MeterPointFlow", "flow"),
            _n("apexclass/Globex", "apexclass"),
        ],
        "edges": [],
    }
    assert orphans(g, types="flow") == ["flow/MeterPointFlow"]
    assert orphans(g, types={"flow", "trigger"}) == [
        "flow/MeterPointFlow",
        "trigger/AcmeTrigger",
    ]


# ------------------------------------------------------------ permission_reachability
def _perm_graph():
    """psg --contains--> PS_Reader --grants--> object/MeterPoint
        profile/Admin --grants--> object/MeterPoint
        PS_Unrelated --grants--> object/Globex
    """
    return {
        "nodes": [
            _n("permsetgroup/AcmeGroup", "permsetgroup"),
            _n("permissionset/PS_Reader", "permissionset"),
            _n("permissionset/PS_Unrelated", "permissionset"),
            _n("profile/Admin", "profile"),
            _n("object/MeterPoint", "object"),
            _n("object/Globex", "object"),
        ],
        "edges": [
            _e("permsetgroup/AcmeGroup", "contains", "permissionset/PS_Reader"),
            _e("permissionset/PS_Reader", "grants", "object/MeterPoint"),
            _e("profile/Admin", "grants", "object/MeterPoint"),
            _e("permissionset/PS_Unrelated", "grants", "object/Globex"),
        ],
    }


def test_permission_reachability_direct_and_group_mediated():
    g = _perm_graph()
    assert permission_reachability(g, "object/MeterPoint") == [
        "permissionset/PS_Reader",
        "profile/Admin",
    ]


def test_permission_reachability_unrelated_target():
    g = _perm_graph()
    assert permission_reachability(g, "object/Globex") == ["permissionset/PS_Unrelated"]


def test_permission_reachability_ignores_nonprincipal_grants_source():
    # A `grants` edge whose source is not a permset/profile is ignored.
    g = {
        "nodes": [
            _n("apexclass/Weird", "apexclass"),
            _n("object/MeterPoint", "object"),
        ],
        "edges": [_e("apexclass/Weird", "grants", "object/MeterPoint")],
    }
    assert permission_reachability(g, "object/MeterPoint") == []
    assert permission_reachability(g, None) == []


# --------------------------------------------------------------------------- summary
def test_graph_summary_counts_cycles_and_orphans():
    g = {
        "nodes": [
            _n("apexclass/Globex", "apexclass"),
            _n("apexclass/Acme", "apexclass"),
            _n("trigger/Orphan", "trigger"),
            _n("object/Ext", "object", external=True),
        ],
        "edges": [
            _e("apexclass/Globex", "calls", "apexclass/Acme"),
            _e("apexclass/Acme", "calls", "apexclass/Globex"),  # A<->B cycle
        ],
    }
    s = graph_summary(g)
    assert s["node_counts"] == {"apexclass": 2, "trigger": 1, "object": 1}
    assert s["edge_counts"] == {"calls": 2}
    assert s["cycle_count"] >= 1            # the Globex<->Acme cycle is detected
    # Orphans: both apexclasses have incoming (cycle), trigger/Orphan has none,
    # object/Ext is external -> excluded. So exactly one orphan.
    assert s["orphan_count"] == 1


def test_graph_summary_empty_graph():
    s = graph_summary({"nodes": [], "edges": []})
    assert s == {
        "node_counts": {},
        "edge_counts": {},
        "cycle_count": 0,
        "orphan_count": 0,
    }
