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


# --- inline-text redaction (confidentiality) -------------------------------- #
_PAGE_GRAPH = {
    "nodes": [
        {"id": "page/ENG/Billing", "type": "page", "label": "Billing",
         "text": "secret body text", "space_key": "ENG"},
        {"id": "object/Account", "type": "object", "label": "Account"},
    ],
    "edges": [], "unresolved": [], "errors": [],
}


def test_text_preserved_by_default():
    # the library primitive stays faithful: round-trip keeps the body
    restored = from_json(to_json(_PAGE_GRAPH))
    page = next(n for n in restored["nodes"] if n["id"] == "page/ENG/Billing")
    assert page["text"] == "secret body text" and "text_redacted" not in page


def test_redact_text_drops_body_and_flags_it():
    data = json.loads(to_json(_PAGE_GRAPH, redact_text=True))
    page = next(n for n in data["nodes"] if n["id"] == "page/ENG/Billing")
    assert "text" not in page and page["text_redacted"] is True
    assert page["space_key"] == "ENG"                      # other attrs survive


def test_redact_leaves_textless_nodes_untouched():
    data = json.loads(to_json(_PAGE_GRAPH, redact_text=True))
    obj = next(n for n in data["nodes"] if n["id"] == "object/Account")
    assert "text_redacted" not in obj


def test_redact_does_not_mutate_input():
    to_json(_PAGE_GRAPH, redact_text=True)
    page = next(n for n in _PAGE_GRAPH["nodes"] if n["id"] == "page/ENG/Billing")
    assert page["text"] == "secret body text" and "text_redacted" not in page


def test_save_graph_redacts_when_asked(tmp_path):
    out = tmp_path / "graph.json"
    save_graph(_PAGE_GRAPH, out, redact_text=True)
    assert "secret body text" not in out.read_text(encoding="utf-8")
    page = next(n for n in load_graph(out)["nodes"] if n["id"] == "page/ENG/Billing")
    assert page.get("text_redacted") is True and "text" not in page


def test_unresolved_and_errors_are_sorted():
    """Determinism covers the whole file: unresolved/errors come out in a fixed
    order regardless of insertion order (a parallel build may interleave them)."""
    u1 = {"src": "b/x", "type": "uses", "to_kind": "k", "to_name": "n", "reason": "r"}
    u2 = {"src": "a/y", "type": "uses", "to_kind": "k", "to_name": "n", "reason": "r"}
    e1 = {"source": "sf", "path": "Zed.cls", "error": "E"}
    e2 = {"source": "sf", "path": "Abc.cls", "error": "E"}
    fwd = persistence.to_jsonable({"nodes": [], "edges": [], "unresolved": [u1, u2], "errors": [e1, e2]})
    rev = persistence.to_jsonable({"nodes": [], "edges": [], "unresolved": [u2, u1], "errors": [e2, e1]})
    assert fwd == rev
    assert fwd["unresolved"][0]["src"] == "a/y"
    assert fwd["errors"][0]["path"] == "Abc.cls"
