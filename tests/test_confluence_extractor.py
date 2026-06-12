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


def test_include_macro_emits_embeds_edge(tmp_path):
    data = {"id": "300", "title": "Composite", "space": {"key": "ENG"},
            "body": {"storage": {"value":
                '<ac:structured-macro ac:name="include"><ac:parameter ac:name="">'
                '<ac:link><ri:page ri:content-title="Shared Block"/></ac:link>'
                '</ac:parameter></ac:structured-macro>'}}}
    _, edges = EX.extract(_w(tmp_path, "300.page.json", data))
    et = _et(edges)
    assert ("page/300", "embeds", "page", "ENG/Shared Block") in et
    assert ("page/300", "links-to", "page", "ENG/Shared Block") in et   # also a link


def test_blogpost_marked_on_node(tmp_path):
    data = {"id": "9", "type": "blogpost", "title": "News", "space": {"key": "ENG"},
            "body": {"storage": {"value": "x"}}}
    nodes, edges = EX.extract(_w(tmp_path, "9.page.json", data))
    page = _ids(nodes)["page/9"]
    assert page["type"] == "page" and page["content_type"] == "blogpost"
    assert ("page/9", "child-of", "space", "ENG") in _et(edges)   # blog posts don't nest


def test_ancestors_attr_root_first(tmp_path):
    data = {**CHILD, "id": "102", "title": "Deep Page",
            "ancestors": [{"id": "100", "title": "Acme Platform"},      # root first,
                          {"id": "101", "title": "MeterPoint Sync"}]}   # parent last
    nodes, edges = EX.extract(_w(tmp_path, "102.page.json", data))
    page = _ids(nodes)["page/102"]
    assert page["ancestors"] == ["100", "101"]            # ids, same order as REST
    assert all(isinstance(a, str) for a in page["ancestors"])
    # the child-of edge still targets the IMMEDIATE parent only
    assert ("page/102", "child-of", "page", "ENG/MeterPoint Sync") in _et(edges)


def test_no_ancestors_attr_on_top_level_page(tmp_path):
    data = {"id": "100", "title": "Acme Platform", "space": {"key": "ENG"},
            "body": {"storage": {"value": "root"}}}
    nodes, _ = EX.extract(_w(tmp_path, "100.page.json", data))
    assert "ancestors" not in _ids(nodes)["page/100"]


def test_created_updated_attrs_and_tolerated_absence(tmp_path):
    rich = {"id": "60", "title": "Spec", "space": {"key": "ENG"},
            "history": {"createdDate": "2024-02-01T09:30:00.000Z"},
            "version": {"number": 2, "when": "2025-11-20T14:05:00.000Z"},
            "body": {"storage": {"value": "x"}}}
    nodes, _ = EX.extract(_w(tmp_path, "60.page.json", rich))
    page = _ids(nodes)["page/60"]
    assert page["created"] == "2024-02-01T09:30:00.000Z"
    assert page["updated"] == "2025-11-20T14:05:00.000Z"

    bare = {"id": "61", "title": "No History", "space": {"key": "ENG"},
            "body": {"storage": {"value": "x"}}}
    nodes, _ = EX.extract(_w(tmp_path, "61.page.json", bare))
    page = _ids(nodes)["page/61"]
    assert "created" not in page and "updated" not in page


def test_tiny_link_attr_only_never_an_edge(tmp_path):
    data = {"id": "70", "title": "Shortcuts", "space": {"key": "ENG"},
            "body": {"storage": {"value":
                '<a href="/x/AbCd9">one</a> '
                '<a href="https://wiki.example.internal/x/AbCd9">same, absolute</a> '
                '<a href="https://wiki.example.internal/x/QwErTz">two</a>'}}}
    nodes, edges = EX.extract(_w(tmp_path, "70.page.json", data))
    page = _ids(nodes)["page/70"]
    # deduped (relative + absolute of the same id collapse), order preserved
    assert page["tiny_links"] == ["AbCd9", "QwErTz"]
    # tiny ids are NOT page ids — no links-to edge may be fabricated from them
    assert not any(e["type"] == "links-to" for e in edges)


def test_status_attr_only_when_not_current(tmp_path):
    archived = {"id": "80", "title": "Old Runbook", "status": "archived",
                "space": {"key": "ENG"}, "body": {"storage": {"value": "x"}}}
    nodes, _ = EX.extract(_w(tmp_path, "80.page.json", archived))
    assert _ids(nodes)["page/80"]["status"] == "archived"

    current = {"id": "81", "title": "Live Page", "status": "current",
               "space": {"key": "ENG"}, "body": {"storage": {"value": "x"}}}
    nodes, _ = EX.extract(_w(tmp_path, "81.page.json", current))
    assert "status" not in _ids(nodes)["page/81"]   # the default is not knowledge


def test_jira_macro_keys_attr_only_no_build_edge(tmp_path):
    data = {"id": "5", "title": "Runbook", "space": {"key": "ENG"},
            "body": {"storage": {"value":
                '<ac:structured-macro ac:name="jira">'
                '<ac:parameter ac:name="key">ACME-101</ac:parameter></ac:structured-macro>'}}}
    nodes, edges = EX.extract(_w(tmp_path, "5.page.json", data))
    page = _ids(nodes)["page/5"]
    assert page["jira_keys"] == ["ACME-101"]
    # cross-source wiring is the deliberate jira.join step, never a build edge
    assert not any(e["to_kind"] == "jiraissue" for e in edges)
