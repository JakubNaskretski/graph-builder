"""List View extractor tests."""
from pathlib import Path

from graphbuilder import core, resolvers
from graphbuilder.extractors.listviews import EXTRACTORS, ListViewExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _listview_xml(full_name, columns, filter_field, filter_value):
    cols = "".join(f"\n  <columns>{c}</columns>" for c in columns)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ListView {NS}>
  <fullName>{full_name}</fullName>
  <label>{full_name}</label>{cols}
  <filterScope>Everything</filterScope>
  <filters>
    <field>{filter_field}</field>
    <operation>equals</operation>
    <value>{filter_value}</value>
  </filters>
</ListView>
"""


def _lv_path(tmp_path, obj, name):
    return tmp_path / "objects" / obj / "listViews" / f"{name}.listView-meta.xml"


def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], ListViewExtractor)


def test_handles_listview_only():
    ex = ListViewExtractor()
    assert ex.handles(Path("Open_Meters.listView-meta.xml"))
    assert not ex.handles(Path("MeterPoint__c.object-meta.xml"))


def test_node_object_ref_and_field_reads(tmp_path):
    p = _lv_path(tmp_path, "MeterPoint__c", "Open_Meters")
    _w(p, _listview_xml("Open_Meters",
                        ["Status__c", "NAME", "Account.Name"],
                        "Status__c", "Open"))
    nodes, edges = ListViewExtractor().extract(p)

    assert nodes == [{
        "id": "listview/MeterPoint__c.Open_Meters",
        "type": "listview", "label": "MeterPoint__c.Open_Meters",
    }]
    triples = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("references", "object", "MeterPoint__c") in triples
    assert ("reads", "field", "MeterPoint__c.Status__c") in triples
    assert ("reads", "field", "Account.Name") in triples          # cross-object hop kept
    # ALL-CAPS pseudo-column NAME is skipped
    assert ("reads", "field", "MeterPoint__c.NAME") not in triples


def test_filter_value_never_emitted(tmp_path):
    p = _lv_path(tmp_path, "MeterPoint__c", "Secret_View")
    _w(p, _listview_xml("Secret_View", ["Token__c"], "Token__c", "do-not-leak"))
    nodes, edges = ListViewExtractor().extract(p)
    assert "do-not-leak" not in (repr(nodes) + repr(edges))
    # the field NAME is still read
    triples = {(e["type"], e["to_name"]) for e in edges}
    assert ("reads", "MeterPoint__c.Token__c") in triples


def test_dedupes_repeated_field(tmp_path):
    p = _lv_path(tmp_path, "MeterPoint__c", "Dup")
    _w(p, _listview_xml("Dup", ["Status__c", "Status__c"], "Status__c", "x"))
    _, edges = ListViewExtractor().extract(p)
    reads = [e for e in edges if e["type"] == "reads" and e["to_name"] == "MeterPoint__c.Status__c"]
    assert len(reads) == 1


def test_broken_xml_still_emits_node_and_object(tmp_path):
    p = _lv_path(tmp_path, "MeterPoint__c", "Broken")
    _w(p, "<ListView><columns>")  # truncated
    nodes, edges = ListViewExtractor().extract(p)
    assert nodes[0]["id"] == "listview/MeterPoint__c.Broken"
    assert any(e["type"] == "references" and e["to_name"] == "MeterPoint__c" for e in edges)


def test_build_resolves_object_and_field(tmp_path):
    p = _lv_path(tmp_path, "MeterPoint__c", "Open")
    _w(p, _listview_xml("Open", ["Status__c"], "Status__c", "Open"))
    gb = core.GraphBuilder().register(*EXTRACTORS)
    gb.register_resolver(*resolvers.default_resolvers())
    g = gb.build(tmp_path)
    assert {"src": "listview/MeterPoint__c.Open",
            "dst": "object/MeterPoint__c", "type": "references"} in g["edges"]
    assert g["errors"] == []
