"""Persistence — save / load / round-trip a built graph as JSON."""
import json

from graphbuilder import persistence, save_graph, load_graph, to_json, from_json

_GRAPH = {
    "nodes": [
        {"id": "object/MeterPoint__c", "type": "object", "label": "MeterPoint__c"},
        {"id": "apexclass/AcmeService", "type": "apexclass", "label": "AcmeService"},
    ],
    "edges": [
        {"src": "apexclass/AcmeService", "dst": "object/MeterPoint__c", "type": "references"},
    ],
    "unresolved": [{"src": "x", "type": "calls", "to_kind": "apexclass",
                    "to_name": "Ghost", "reason": "unresolved target"}],
    "errors": [],
}


def _by_id(nodes):
    return sorted(nodes, key=lambda n: n["id"])


def test_round_trip_preserves_graph():
    # serialisation sorts deterministically, so compare order-insensitively
    restored = from_json(to_json(_GRAPH))
    assert _by_id(restored["nodes"]) == _by_id(_GRAPH["nodes"])
    assert restored["edges"] == _GRAPH["edges"]
    assert restored["unresolved"] == _GRAPH["unresolved"]
    assert restored["errors"] == _GRAPH["errors"]


def test_save_and_load_file(tmp_path):
    out = tmp_path / "sub" / "graph.json"      # parent dir created
    returned = save_graph(_GRAPH, out)
    assert returned == out and out.exists()
    loaded = load_graph(out)
    assert _by_id(loaded["nodes"]) == _by_id(_GRAPH["nodes"])
    assert loaded["edges"] == _GRAPH["edges"]


def test_on_disk_form_is_versioned():
    data = json.loads(to_json(_GRAPH))
    assert data["version"] == persistence.SCHEMA_VERSION
    assert set(data) >= {"version", "nodes", "edges", "unresolved", "errors"}


def test_output_is_deterministic():
    """Node/edge order in the input must not change the serialised bytes."""
    shuffled = {
        "nodes": list(reversed(_GRAPH["nodes"])),
        "edges": _GRAPH["edges"],
        "unresolved": _GRAPH["unresolved"],
        "errors": _GRAPH["errors"],
    }
    assert to_json(_GRAPH) == to_json(shuffled)


def test_load_tolerates_bare_and_partial():
    # bare {nodes, edges} with no version / unresolved / errors
    bare = from_json(json.dumps({"nodes": [{"id": "a", "type": "object"}], "edges": []}))
    assert bare["nodes"] == [{"id": "a", "type": "object"}]
    assert bare["unresolved"] == [] and bare["errors"] == []
    # non-dict JSON degrades to an empty graph, never raises
    assert from_json("[]") == {"nodes": [], "edges": [], "unresolved": [], "errors": []}


def test_none_graph_serialises_empty():
    assert from_json(to_json(None)) == {"nodes": [], "edges": [], "unresolved": [], "errors": []}
