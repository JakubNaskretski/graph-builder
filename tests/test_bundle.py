"""Knowledge-base bundle tests — offline, synthetic, fictional names.

Builds a tiny force-app + Confluence dump, bundles them, and asserts the pointer
model, content layout, SF unit copying (incl. that un-graphed/leakage-prone files
are never bundled), the cross-source `documents` edge, the manifest, and that full
body content stays OUT of graph.json.
"""
import json
import zipfile
from pathlib import Path

import pytest

from graphbuilder import load_graph
from graphbuilder.bundle import build_bundle

FILLER = "filler " * 60  # > 280 chars, so the tail marker lands past the excerpt cutoff


def _scaffold(root: Path):
    fa = root / "force-app" / "main" / "default"
    (fa / "classes").mkdir(parents=True)
    (fa / "classes" / "Foo.cls").write_text(
        "public class Foo { public void bar() { Acme__c a = new Acme__c(); } }", "utf-8")
    obj = fa / "objects" / "Acme__c"
    (obj / "fields").mkdir(parents=True)
    (obj / "Acme__c.object-meta.xml").write_text(
        '<?xml version="1.0"?><CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">'
        "<label>Acme</label></CustomObject>", "utf-8")
    (obj / "fields" / "Note__c.field-meta.xml").write_text(
        '<?xml version="1.0"?><CustomField xmlns="http://soap.sforce.com/2006/04/metadata">'
        "<fullName>Note__c</fullName><type>Text</type></CustomField>", "utf-8")
    # un-graphed, leakage-prone file: must never be copied into the bundle
    (fa / "staticresources").mkdir(parents=True)
    (fa / "staticresources" / "secret.txt").write_text("placeholder content (un-graphed file; must not be bundled)", "utf-8")

    dump = root / "confluence-dump" / "ENG"
    dump.mkdir(parents=True)
    (dump / "100.page.json").write_text(json.dumps({
        "id": "100", "title": "Acme Overview", "space": {"key": "ENG"},
        "version": {"number": 3, "by": {"userKey": "u-jdoe"}},
        "body": {"storage": {"value":
            f"<p>{FILLER} TAILMARKER_UNIQUE about Acme.</p> "
            'See <a href="https://x.lightning.force.com/lightning/o/Acme__c/list">Acme</a> '
            'and <ac:link><ri:page ri:content-title="Detail"/></ac:link>.'}},
    }), "utf-8")
    return fa, dump


@pytest.fixture
def kb(tmp_path):
    fa, dump = _scaffold(tmp_path)
    out = tmp_path / "knowledge-base"
    summary = build_bundle(out, salesforce=fa, confluence_dump=dump,
                           created_at="2026-06-07T00:00:00+00:00")
    return {"out": out, "summary": summary, "graph": load_graph(out / "graph.json")}


def _ids(graph):
    return {n["id"]: n for n in graph["nodes"]}


def test_bundle_tree_and_zip(kb):
    out = kb["out"]
    for f in ("manifest.json", "graph.json", "README.txt"):
        assert (out / f).exists()
    z = kb["summary"]["zip"]
    assert z and Path(z).exists()
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
    assert {"graph.json", "manifest.json", "README.txt", "content/confluence/ENG/100.txt"} <= names


def test_confluence_pointer_model(kb):
    page = _ids(kb["graph"])["page/ENG/Acme Overview"]
    assert "text" not in page                                   # inline body removed
    assert page["content"] == "content/confluence/ENG/100.txt"
    assert page["content_xhtml"] == "content/confluence/ENG/100.xhtml"
    assert page["excerpt"] and len(page["excerpt"]) <= 280
    assert "TAILMARKER_UNIQUE" in (kb["out"] / page["content"]).read_text("utf-8")
    assert "ri:page" in (kb["out"] / page["content_xhtml"]).read_text("utf-8")  # raw storage kept


def test_salesforce_units_copied(kb):
    out, ids = kb["out"], _ids(kb["graph"])
    cls = ids["apexclass/Foo"]["content"]
    obj = ids["object/Acme__c"]["content"]
    assert cls.endswith("classes/Foo.cls") and (out / cls).is_file()
    assert obj.endswith("objects/Acme__c") and (out / obj).is_dir()             # object -> folder unit
    assert (out / obj / "fields" / "Note__c.field-meta.xml").is_file()
    assert not list((out / "content" / "salesforce").rglob("secret.txt"))       # leakage-prone, never bundled


def test_documents_cross_edge(kb):
    docs = {(e["src"], e["dst"]) for e in kb["graph"]["edges"] if e["type"] == "documents"}
    assert ("page/ENG/Acme Overview", "object/Acme__c") in docs


def test_manifest(kb):
    m = kb["summary"]["manifest"]
    assert m["schema_version"] == 1
    assert m["graph"]["documents_edges"] == 1
    assert m["sources"]["confluence"]["pages"] == 1
    assert m["sources"]["salesforce"]["units"] >= 2


def test_confidentiality_full_body_not_in_graph(kb):
    gj = (kb["out"] / "graph.json").read_text("utf-8")
    assert "TAILMARKER_UNIQUE" not in gj          # full body stays in content files only
    assert "SECRET-ENDPOINT" not in gj            # un-graphed source never enters the graph


def test_salesforce_only(tmp_path):
    fa, _ = _scaffold(tmp_path)
    out = tmp_path / "kb-sf"
    s = build_bundle(out, salesforce=fa, zip_path=False)
    g = load_graph(out / "graph.json")
    assert any(n["type"] == "apexclass" for n in g["nodes"])
    assert not any(n.get("type") == "page" for n in g["nodes"])
    assert s["manifest"]["graph"]["documents_edges"] == 0
    assert s["zip"] is None


def test_confluence_only(tmp_path):
    _, dump = _scaffold(tmp_path)
    out = tmp_path / "kb-c"
    build_bundle(out, confluence_dump=dump, zip_path=False)
    g = load_graph(out / "graph.json")
    assert any(n["type"] == "page" for n in g["nodes"])
    assert not any(n.get("type") == "apexclass" for n in g["nodes"])


def test_no_sources_raises(tmp_path):
    with pytest.raises(ValueError):
        build_bundle(tmp_path / "x")


def test_no_zip(tmp_path):
    fa, dump = _scaffold(tmp_path)
    out = tmp_path / "kb-nozip"
    s = build_bundle(out, salesforce=fa, confluence_dump=dump, zip_path=False)
    assert s["zip"] is None and not (tmp_path / "kb-nozip.zip").exists()


def test_parallel_equals_serial(tmp_path):
    fa, dump = _scaffold(tmp_path)

    def sig(out):
        g = load_graph(out / "graph.json")
        return (sorted(n["id"] for n in g["nodes"]),
                sorted((e["src"], e["type"], e["dst"]) for e in g["edges"]))

    build_bundle(tmp_path / "s", salesforce=fa, confluence_dump=dump, zip_path=False,
                 created_at="2026-06-07T00:00:00+00:00", parallel=False)
    build_bundle(tmp_path / "p", salesforce=fa, confluence_dump=dump, zip_path=False,
                 created_at="2026-06-07T00:00:00+00:00", parallel=True)
    assert sig(tmp_path / "s") == sig(tmp_path / "p")
