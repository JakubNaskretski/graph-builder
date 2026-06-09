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
    page = ids["page/101"]                      # id-keyed (rename-stable); title = label
    assert page["type"] == "page" and page["source"] == "confluence"
    assert page["label"] == "MeterPoint Sync"
    assert page["page_id"] == "101" and page["version"] == 7 and page["space_key"] == "ENG"
    assert "MeterPoint__c" in " ".join(page["urls"]) and page.get("text")
    assert "space/ENG" in ids
    assert "attachment/101/mapping.csv" in ids  # keyed by owning page id, not title
    assert "confluencelabel/integration" in ids
    assert "confluenceuser/u-msmith" in ids and "confluenceuser/u-jdoe" in ids


def test_extract_edges(tmp_path):
    _, edges = EX.extract(_w(tmp_path, "101.page.json", CHILD))
    et = _et(edges)
    pid = "page/101"
    # page targets are still NAMED in title form (all the markup carries);
    # PageResolver maps them back to the id-keyed nodes at resolve time
    assert (pid, "child-of", "page", "ENG/Acme Platform") in et
    assert (pid, "links-to", "page", "ENG/Acme Platform") in et
    assert (pid, "attaches", "attachment", "101/mapping.csv") in et
    assert (pid, "labeled", "confluencelabel", "integration") in et
    assert (pid, "mentions", "confluenceuser", "u-msmith") in et
    assert (pid, "authored-by", "confluenceuser", "u-jdoe") in et


def test_top_level_page_is_child_of_space(tmp_path):
    data = {"id": "100", "title": "Acme Platform", "space": {"key": "ENG"},
            "body": {"storage": {"value": "root page"}}}
    _, edges = EX.extract(_w(tmp_path, "100.page.json", data))
    assert ("page/100", "child-of", "space", "ENG") in _et(edges)


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

    # title-form link/parent targets resolve to the REAL id-keyed parent node
    assert ids["page/100"].get("external") is not True
    assert ("page/101", "links-to", "page/100") in edges
    assert ("page/101", "child-of", "page/100") in edges
    # the one shared label node is reached from both pages
    assert ("page/100", "labeled", "confluencelabel/architecture") in edges
    assert ("page/101", "labeled", "confluencelabel/architecture") in edges


def test_build_graph_external_stub_for_missing_link(tmp_path):
    dump = tmp_path / "dump" / "ENG"
    _w(dump, "101.page.json", CHILD)   # links to "Acme Platform", which is NOT collected
    g = (GraphBuilder().register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    ids = {n["id"]: n for n in g["nodes"]}
    # an uncollected target stays title-keyed (its page id is unknowable here)
    assert ids["page/ENG/Acme Platform"].get("external") is True
    assert ("page/101", "links-to", "page/ENG/Acme Platform") in {
        (e["src"], e["type"], e["dst"]) for e in g["edges"]}


def test_rename_keeps_page_identity(tmp_path):
    """The id-keyed node survives a title change: re-extracting the same page id
    under a new title yields the SAME node id (fresh label), so a re-dump can
    never duplicate a renamed page."""
    renamed = {**CHILD, "title": "MeterPoint Sync v2"}
    nodes, _ = EX.extract(_w(tmp_path, "101.page.json", renamed))
    page = _ids(nodes)["page/101"]
    assert page["label"] == "MeterPoint Sync v2"


def test_cross_space_same_title_pages_stay_distinct(tmp_path):
    """Two collected pages with the same (slugged) title in different spaces are
    different nodes — id-keyed identity subsumes the old cross-space slug clash."""
    a = {"id": "1", "title": "Setup", "space": {"key": "ENG"},
         "body": {"storage": {"value": "a"}}}
    b = {"id": "2", "title": "Setup", "space": {"key": "OPS"},
         "body": {"storage": {"value": "b"}}}
    g = (GraphBuilder().register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build_files([_w(tmp_path, "ENG/1.page.json", a),
                       _w(tmp_path, "OPS/2.page.json", b)]))
    ids = {n["id"] for n in g["nodes"]}
    assert {"page/1", "page/2"} <= ids
