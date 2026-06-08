"""Confluence extractor tests — extract() + a full build_graph(tmp_path).

Fictional fixtures only (Acme / MeterPoint, space ENG).
"""
import json
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.confluence import ConfluenceExtractor

EX = ConfluenceExtractor()


def _w(tmp: Path, name: str, data) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), "utf-8")
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


CHILD = {
    "id": "101", "title": "MeterPoint Sync", "space": {"key": "ENG"},
    "ancestors": [{"id": "100", "title": "Acme Platform"}],
    "version": {"number": 7, "by": {"userKey": "u-jdoe"}},
    "metadata": {"labels": {"results": [{"name": "integration"}]}},
    "body": {"storage": {"value":
        'Parent <ac:link><ri:page ri:content-title="Acme Platform"/></ac:link>; '
        'file <ac:link><ri:attachment ri:filename="mapping.csv"/></ac:link>; '
        'cc <ri:user ri:userkey="u-msmith"/>; '
        'org <a href="https://acme.lightning.force.com/lightning/o/MeterPoint__c/view">link</a>.'}},
    "_links": {"base": "https://wiki.example.internal", "webui": "/x"},
}


def test_handles():
    assert EX.handles(Path("a/101.page.json")) is True
    assert EX.handles(Path("a/graph.json")) is False
    assert EX.handles(Path("a/Foo.cls")) is False


def test_extract_nodes_and_attrs(tmp_path):
    nodes, _ = EX.extract(_w(tmp_path, "101.page.json", CHILD))
    ids = _ids(nodes)
    page = ids["page/ENG/MeterPoint Sync"]
    assert page["type"] == "page" and page["source"] == "confluence"
    assert page["page_id"] == "101" and page["version"] == 7 and page["space_key"] == "ENG"
    assert "MeterPoint__c" in " ".join(page["urls"]) and page.get("text")
    assert "space/ENG" in ids
    assert "attachment/ENG/MeterPoint Sync/mapping.csv" in ids
    assert "confluencelabel/integration" in ids
    assert "confluenceuser/u-msmith" in ids and "confluenceuser/u-jdoe" in ids


def test_extract_edges(tmp_path):
    _, edges = EX.extract(_w(tmp_path, "101.page.json", CHILD))
    et = _et(edges)
    pid = "page/ENG/MeterPoint Sync"
    assert (pid, "child-of", "page", "ENG/Acme Platform") in et
    assert (pid, "links-to", "page", "ENG/Acme Platform") in et
    assert (pid, "attaches", "attachment", "ENG/MeterPoint Sync/mapping.csv") in et
    assert (pid, "labeled", "confluencelabel", "integration") in et
    assert (pid, "mentions", "confluenceuser", "u-msmith") in et
    assert (pid, "authored-by", "confluenceuser", "u-jdoe") in et


def test_top_level_page_is_child_of_space(tmp_path):
    data = {"id": "100", "title": "Acme Platform", "space": {"key": "ENG"},
            "body": {"storage": {"value": "root page"}}}
    _, edges = EX.extract(_w(tmp_path, "100.page.json", data))
    assert ("page/ENG/Acme Platform", "child-of", "space", "ENG") in _et(edges)


def test_self_link_is_skipped(tmp_path):
    data = {"id": "1", "title": "Self", "space": {"key": "ENG"},
            "body": {"storage": {"value": '<ac:link><ri:page ri:content-title="Self"/></ac:link>'}}}
    _, edges = EX.extract(_w(tmp_path, "1.page.json", data))
    assert not any(e["type"] == "links-to" for e in edges)


def test_never_raises_on_broken_content(tmp_path):
    data = {"id": "9", "title": "Broken", "space": {"key": "ENG"},
            "body": {"storage": {"value": "<ri:page <<< ri:filename=  &nbsp;"}}}
    nodes, edges = EX.extract(_w(tmp_path, "9.page.json", data))   # must not raise
    assert any(n["type"] == "page" for n in nodes)
    assert isinstance(edges, list)


def test_build_graph_resolves_links_and_shares_labels(tmp_path):
    dump = tmp_path / "dump" / "ENG"
    _w(dump, "100.page.json", {"id": "100", "title": "Acme Platform", "space": {"key": "ENG"},
        "metadata": {"labels": {"results": [{"name": "architecture"}]}},
        "body": {"storage": {"value": "root"}}})
    _w(dump, "101.page.json", {**CHILD,
        "metadata": {"labels": {"results": [{"name": "architecture"}]}}})

    g = (GraphBuilder().register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    assert g["errors"] == []
    ids = {n["id"]: n for n in g["nodes"]}
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}

    # links-to / child-of resolve to the REAL parent page (both dumps present)
    assert ids["page/ENG/Acme Platform"].get("external") is not True
    assert ("page/ENG/MeterPoint Sync", "links-to", "page/ENG/Acme Platform") in edges
    assert ("page/ENG/MeterPoint Sync", "child-of", "page/ENG/Acme Platform") in edges
    # the one shared label node is reached from both pages
    assert ("page/ENG/Acme Platform", "labeled", "confluencelabel/architecture") in edges
    assert ("page/ENG/MeterPoint Sync", "labeled", "confluencelabel/architecture") in edges


def test_build_graph_external_stub_for_missing_link(tmp_path):
    dump = tmp_path / "dump" / "ENG"
    _w(dump, "101.page.json", CHILD)   # links to "Acme Platform", which is NOT collected
    g = (GraphBuilder().register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    ids = {n["id"]: n for n in g["nodes"]}
    assert ids["page/ENG/Acme Platform"].get("external") is True
