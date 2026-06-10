"""Confluence -> Salesforce join tests (page -> SF entity it documents).

Fictional fixtures only.
"""
from graphbuilder.confluence.join import join, merge

SF = {"nodes": [
        {"id": "object/MeterPoint__c", "type": "object", "label": "MeterPoint__c"},
        {"id": "object/Account", "type": "object", "label": "Account"},
        {"id": "apexclass/MeterPointService", "type": "apexclass", "label": "MeterPointService"},
      ], "edges": [], "unresolved": [], "errors": []}


def _cgraph(nodes, edges=None):
    return {"nodes": nodes, "edges": edges or [], "unresolved": [], "errors": []}


def _page(pid, label, text="", urls=None, ntype="page"):
    n = {"id": pid, "type": ntype, "label": label}
    if text:
        n["text"] = text
    if urls:
        n["urls"] = urls
    return n


def _tuples(edges):
    return {(e["src"], e["dst"], e["via"], e["confidence"]) for e in edges}


def test_join_url_high_confidence():
    g = _cgraph([_page("page/ENG/Sync", "Sync",
                       urls=["https://x.lightning.force.com/lightning/o/MeterPoint__c/list"])])
    assert join(g, SF) == [{"src": "page/ENG/Sync", "type": "documents",
                            "dst": "object/MeterPoint__c", "via": "url", "confidence": "high"}]


def test_join_title_exact_match_medium():
    g = _cgraph([_page("page/ENG/Account", "Account")])
    assert ("page/ENG/Account", "object/Account", "title", "medium") in _tuples(join(g, SF))


def test_join_no_false_positive_on_partial_title():
    g = _cgraph([_page("page/ENG/Account Notes", "Account Notes")])
    assert join(g, SF) == []


def test_join_labels_off_by_default():
    g = _cgraph(
        [_page("page/ENG/Doc", "Doc"),
         {"id": "confluencelabel/Account", "type": "confluencelabel", "label": "Account"}],
        edges=[{"src": "page/ENG/Doc", "type": "labeled", "dst": "confluencelabel/Account"}],
    )
    assert join(g, SF) == []                                   # off by default
    assert any(e["dst"] == "object/Account" and e["via"] == "label"
               for e in join(g, SF, match_labels=True))        # on when asked


def test_join_scan_body_off_by_default():
    g = _cgraph([_page("page/ENG/Notes", "Notes", text="The MeterPoint__c sync runs nightly.")])
    assert join(g, SF) == []                                   # off by default
    assert any(e["dst"] == "object/MeterPoint__c" and e["via"] == "body"
               for e in join(g, SF, scan_body=True))           # on when asked


def test_join_dedup_keeps_highest_confidence():
    g = _cgraph([_page("page/ENG/MeterPoint__c", "MeterPoint__c",
                       urls=["https://x.lightning.force.com/lightning/o/MeterPoint__c/list"])])
    hits = [e for e in join(g, SF) if e["dst"] == "object/MeterPoint__c"]
    assert len(hits) == 1 and hits[0]["confidence"] == "high"   # url(high) beats title(medium)


def test_join_min_len_guard():
    sf = {"nodes": [{"id": "object/X", "type": "object", "label": "X"}], "edges": []}
    assert join(_cgraph([_page("page/ENG/X", "X")]), sf) == []


def test_join_ignores_non_page_nodes():
    assert join(_cgraph([_page("space/ENG", "ENG", ntype="space")]), SF) == []


def test_merge_unions_and_does_not_mutate():
    g = _cgraph([_page("page/ENG/Sync", "Sync",
                       urls=["https://x.lightning.force.com/lightning/o/MeterPoint__c/list"])])
    cross = join(g, SF)
    sf_edges_before, c_nodes_before = len(SF["edges"]), len(g["nodes"])
    m = merge(SF, g, cross)
    assert len(m["nodes"]) == len(SF["nodes"]) + len(g["nodes"])
    assert len(m["edges"]) == len(SF["edges"]) + len(g["edges"]) + len(cross)
    assert any(e["type"] == "documents" for e in m["edges"])
    assert len(SF["edges"]) == sf_edges_before and len(g["nodes"]) == c_nodes_before  # inputs intact


def test_join_and_merge_tolerate_empty():
    assert join({}, {}) == [] and join(None, None) == []
    assert merge(None, None, None)["nodes"] == []


def test_join_named_tab_and_object_manager_urls():
    sf = {"nodes": [
        {"id": "tab/Acme_Console", "type": "tab", "label": "Acme_Console"},
        {"id": "object/MeterPoint__c", "type": "object", "label": "MeterPoint__c"},
    ], "edges": []}
    g = _cgraph([_page("page/1", "Runbook", urls=[
        "https://x.lightning.force.com/lightning/n/Acme_Console",
        "https://x.lightning.force.com/lightning/setup/ObjectManager/MeterPoint__c/FieldsAndRelationships/view",
    ])])
    t = _tuples(join(g, sf))
    assert ("page/1", "tab/Acme_Console", "url", "high") in t
    assert ("page/1", "object/MeterPoint__c", "url", "high") in t


def test_join_same_confidence_tie_break_is_deterministic():
    """title and body hits are both medium; the winning via must come from the
    fixed via ranking (title > body), never from scan order."""
    g = _cgraph([_page("page/1", "MeterPoint__c", text="MeterPoint__c sync notes")])
    hits = [e for e in join(g, SF, scan_body=True) if e["dst"] == "object/MeterPoint__c"]
    assert len(hits) == 1 and hits[0]["via"] == "title"
