"""Jira extractor tests — extract() + a full build_graph(tmp_path).

Fictional fixtures only (Acme, project ACME).
"""
import json
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.jira import JiraExtractor

EX = JiraExtractor()


def _w(tmp: Path, name: str, data) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), "utf-8")
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


BUG = {
    "id": "10001", "key": "ACME-101",
    "fields": {
        "summary": "MeterPoint sync drops readings",
        "description": "Fails for MeterPoint__c. cc [~jdoe]",
        "issuetype": {"name": "Bug"}, "status": {"name": "Open"},
        "labels": ["billing"],
        "assignee": {"name": "msmith"}, "reporter": {"name": "jdoe"},
        "project": {"key": "ACME", "name": "Acme Platform"},
        "updated": "2026-06-01T10:00:00.000+0000",
        "issuelinks": [{"type": {"name": "Blocks"}, "outwardIssue": {"key": "ACME-102"}}],
        "subtasks": [{"key": "ACME-103"}],
    },
}


def test_handles():
    assert EX.handles(Path("a/ACME-101.issue.json")) is True
    assert EX.handles(Path("a/101.page.json")) is False
    assert EX.handles(Path("a/Foo.cls")) is False


def test_extract_nodes_and_attrs(tmp_path):
    nodes, _ = EX.extract(_w(tmp_path, "ACME-101.issue.json", BUG))
    ids = _ids(nodes)
    issue = ids["jiraissue/ACME-101"]
    assert issue["type"] == "jiraissue" and issue["source"] == "jira"
    assert issue["label"] == "MeterPoint sync drops readings"   # summary as label
    assert issue["project_key"] == "ACME" and issue["issue_type"] == "Bug"
    assert issue["status"] == "Open" and issue.get("text")
    assert ids["jiraproject/ACME"]["label"] == "Acme Platform"
    assert "jiralabel/billing" in ids
    assert "jirauser/msmith" in ids and "jirauser/jdoe" in ids


def test_extract_edges(tmp_path):
    _, edges = EX.extract(_w(tmp_path, "ACME-101.issue.json", BUG))
    et = _et(edges)
    iid = "jiraissue/ACME-101"
    assert (iid, "child-of", "jiraproject", "ACME") in et
    assert (iid, "links-to", "jiraissue", "ACME-102") in et      # issue link
    assert (iid, "links-to", "jiraissue", "ACME-103") in et      # subtask
    assert (iid, "labeled", "jiralabel", "billing") in et
    assert (iid, "assigned-to", "jirauser", "msmith") in et
    assert (iid, "authored-by", "jirauser", "jdoe") in et
    assert (iid, "mentions", "jirauser", "jdoe") in et


def test_subtask_is_child_of_parent_issue(tmp_path):
    sub = {"key": "ACME-103", "fields": {
        "summary": "Subtask", "issuetype": {"name": "Sub-task"},
        "project": {"key": "ACME"}, "parent": {"key": "ACME-101"}}}
    _, edges = EX.extract(_w(tmp_path, "ACME-103.issue.json", sub))
    et = _et(edges)
    assert ("jiraissue/ACME-103", "child-of", "jiraissue", "ACME-101") in et
    assert not any(e[1] == "child-of" and e[2] == "jiraproject" for e in et)


def test_build_graph_resolves_links_and_stubs(tmp_path):
    dump = tmp_path / "jira-dump" / "ACME"
    _w(dump, "ACME-101.issue.json", BUG)
    _w(dump, "ACME-102.issue.json", {"key": "ACME-102", "fields": {
        "summary": "Blocked work", "project": {"key": "ACME"}}})
    g = (GraphBuilder().register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    assert g["errors"] == [] and g["unresolved"] == []
    ids = {n["id"]: n for n in g["nodes"]}
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    # collected link target resolves to the real node; uncollected becomes a stub
    assert ids["jiraissue/ACME-102"].get("external") is not True
    assert ("jiraissue/ACME-101", "links-to", "jiraissue/ACME-102") in edges
    assert ids["jiraissue/ACME-103"].get("external") is True


def test_never_raises_on_broken_content(tmp_path):
    p = tmp_path / "ACME-9.issue.json"
    p.write_text('{"key": "ACME-9", "fields": {"description": 42, "issuelinks": [null]}}', "utf-8")
    nodes, edges = EX.extract(p)    # must not raise
    assert any(n["type"] == "jiraissue" for n in nodes)
    assert isinstance(edges, list)
