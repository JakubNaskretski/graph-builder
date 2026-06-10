"""Jira join tests — issue -> SF and issue <-> Confluence page.

Fictional fixtures only.
"""
from graphbuilder.jira.join import join, join_confluence

SF = {"nodes": [
        {"id": "object/MeterPoint__c", "type": "object", "label": "MeterPoint__c"},
        {"id": "object/Account", "type": "object", "label": "Account"},
      ], "edges": []}


def _issue(iid, label, text="", urls=None, external=False):
    n = {"id": iid, "type": "jiraissue", "label": label}
    if text:
        n["text"] = text
    if urls:
        n["urls"] = urls
    if external:
        n["external"] = True
    return n


def _graph(nodes, edges=None):
    return {"nodes": nodes, "edges": edges or [], "unresolved": [], "errors": []}


def test_join_issue_to_sf_via_lightning_url():
    g = _graph([_issue("jiraissue/ACME-1", "Sync bug",
                       urls=["https://x.lightning.force.com/lightning/o/MeterPoint__c/list"])])
    edges = join(g, SF)
    assert edges == [{"src": "jiraissue/ACME-1", "type": "documents",
                      "dst": "object/MeterPoint__c", "via": "url", "confidence": "high"}]


def test_join_summary_title_match_off_by_default():
    g = _graph([_issue("jiraissue/ACME-2", "Account")])    # summary == an SF name
    assert join(g, SF) == []                               # flukes stay out
    assert join(g, SF, match_titles=True)                  # opt-in works


def test_join_confluence_issue_to_page_by_page_id():
    jira = _graph([_issue("jiraissue/ACME-1", "Bug",
                          urls=["https://wiki.example.internal/pages/viewpage.action?pageId=100"])])
    conf = _graph([{"id": "page/100", "type": "page", "label": "Acme Overview",
                    "page_id": "100", "space_key": "ENG"}])
    edges = join_confluence(jira, conf)
    assert edges == [{"src": "jiraissue/ACME-1", "type": "links-to", "dst": "page/100",
                      "via": "url", "confidence": "high"}]


def test_join_confluence_issue_to_page_by_display_url():
    jira = _graph([_issue("jiraissue/ACME-1", "Bug",
                          urls=["https://wiki.example.internal/display/ENG/MeterPoint+Sync"])])
    conf = _graph([{"id": "page/101", "type": "page", "label": "MeterPoint Sync",
                    "page_id": "101", "space_key": "ENG"}])
    assert [e["dst"] for e in join_confluence(jira, conf)] == ["page/101"]


def test_join_confluence_page_to_issue_by_jira_macro():
    jira = _graph([_issue("jiraissue/ACME-1", "Bug")])
    conf = _graph([{"id": "page/100", "type": "page", "label": "Runbook",
                    "page_id": "100", "space_key": "ENG", "jira_keys": ["ACME-1", "GONE-9"]}])
    edges = join_confluence(jira, conf)
    assert edges == [{"src": "page/100", "type": "links-to", "dst": "jiraissue/ACME-1",
                      "via": "jira-macro", "confidence": "high"}]   # GONE-9 not collected -> no edge


def test_join_confluence_ignores_external_stubs_and_unknown_urls():
    jira = _graph([
        _issue("jiraissue/ACME-1", "Bug", urls=["https://other.example/nothing"]),
        _issue("jiraissue/EXT-1", "Stub", external=True,
               urls=["https://wiki.example.internal/pages/viewpage.action?pageId=100"]),
    ])
    conf = _graph([{"id": "page/100", "type": "page", "label": "P",
                    "page_id": "100", "space_key": "ENG"}])
    assert join_confluence(jira, conf) == []


def test_joins_tolerate_empty():
    assert join({}, {}) == [] and join(None, None) == []
    assert join_confluence({}, {}) == [] and join_confluence(None, None) == []
