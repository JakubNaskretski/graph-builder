"""apply_classifications — agent read-cold write-back. Fictional fixtures only."""
from graphbuilder.confluence import apply_classifications


def _graph():
    return {
        "nodes": [
            {"id": "page/ENG/Bill", "type": "page", "label": "Bill", "text": "x"},
            {"id": "object/Acme__c", "type": "object", "label": "Acme__c"},
            {"id": "object/Account", "type": "object", "label": "Account"},
        ],
        "edges": [{"src": "page/ENG/Bill", "type": "documents", "dst": "object/Account",
                   "via": "url", "confidence": "high"}],
        "unresolved": [], "errors": [],
    }


def _docs(g):
    return {(e["src"], e["dst"]): e for e in g["edges"] if e["type"] == "documents"}


def test_agent_edge_added_with_evidence():
    g, _ = apply_classifications(_graph(), [{"page_id": "page/ENG/Bill",
        "documents": [{"target": "object/Acme__c", "confidence": "high", "evidence": "about Acme"}]}])
    e = _docs(g)[("page/ENG/Bill", "object/Acme__c")]
    assert e["via"] == "agent" and e["confidence"] == "high" and e["evidence"] == "about Acme"


def test_agent_supersedes_syntactic_pair():
    g, _ = apply_classifications(_graph(), [{"page_id": "page/ENG/Bill",
        "documents": [{"target": "object/Account", "confidence": "medium"}]}])
    docs = _docs(g)
    assert sum(1 for (_s, d) in docs if d == "object/Account") == 1      # one edge per pair
    assert docs[("page/ENG/Bill", "object/Account")]["via"] == "agent"   # agent wins


def test_unknown_target_skipped_and_reported():
    g, rep = apply_classifications(_graph(), [{"page_id": "page/ENG/Bill",
        "documents": [{"target": "object/Ghost"}]}])
    assert ("page/ENG/Bill", "object/Ghost") not in _docs(g)
    assert any(s["reason"] == "unknown target" for s in rep["skipped"])


def test_unknown_page_skipped():
    _g, rep = apply_classifications(_graph(), [{"page_id": "page/ENG/Nope", "process_type": "x"}])
    assert any(s["reason"] == "unknown page_id" for s in rep["skipped"])


def test_page_attrs_set():
    g, rep = apply_classifications(_graph(), [{"page_id": "page/ENG/Bill",
        "process_type": "order-to-cash", "topics": ["billing", "invoicing"]}])
    page = {n["id"]: n for n in g["nodes"]}["page/ENG/Bill"]
    assert page["process_type"] == "order-to-cash" and page["topics"] == ["billing", "invoicing"]
    assert rep["updated_pages"] == 1


def test_invalid_confidence_defaults_to_medium():
    g, _ = apply_classifications(_graph(), [{"page_id": "page/ENG/Bill",
        "documents": [{"target": "object/Acme__c", "confidence": "bogus"}]}])
    assert _docs(g)[("page/ENG/Bill", "object/Acme__c")]["confidence"] == "medium"


def test_input_graph_not_mutated():
    g0 = _graph()
    apply_classifications(g0, [{"page_id": "page/ENG/Bill", "process_type": "x",
                               "documents": [{"target": "object/Acme__c"}]}])
    assert "process_type" not in {n["id"]: n for n in g0["nodes"]}["page/ENG/Bill"]
    assert len([e for e in g0["edges"] if e["type"] == "documents"]) == 1  # original untouched


def test_tolerant_of_malformed_verdicts():
    g, rep = apply_classifications(_graph(),
        ["notadict", {"no_page": 1}, {"page_id": "page/ENG/Bill", "documents": ["x"]}])
    assert isinstance(g, dict) and rep["skipped"]
