"""Custom Metadata records extractor.

Covers node/reference extraction and the guarantee that record field names and
values are never emitted.
"""
from pathlib import Path

from graphbuilder import core, resolvers
from graphbuilder.extractors.custommetadata import EXTRACTORS, CustomMetadataExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _record_xml(label, protected, field, value):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomMetadata {NS}>
  <label>{label}</label>
  <protected>{protected}</protected>
  <values>
    <field>{field}</field>
    <value xsi:type="xsd:string" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{value}</value>
  </values>
</CustomMetadata>
"""


def _build(repo):
    gb = core.GraphBuilder().register(*EXTRACTORS)
    gb.register_resolver(*resolvers.default_resolvers())
    return gb.build(repo)


def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], CustomMetadataExtractor)
    assert EXTRACTORS[0].source == "salesforce"


def test_handles_md_meta_only():
    ex = CustomMetadataExtractor()
    assert ex.handles(Path("Acme_Rate.Standard.md-meta.xml"))
    assert not ex.handles(Path("Acme_Rate__mdt.object-meta.xml"))
    assert not ex.handles(Path("AcmeService.cls"))


def test_record_node_and_reference_to_type(tmp_path):
    p = tmp_path / "Acme_Rate.Standard_Rate.md-meta.xml"
    _w(p, _record_xml("Standard Rate", "false", "Amount__c", "0.42"))
    nodes, edges = CustomMetadataExtractor().extract(p)

    assert nodes == [{
        "id": "custommetadatarecord/Acme_Rate.Standard_Rate",
        "type": "custommetadatarecord",
        "label": "Acme_Rate.Standard_Rate",
        "protected": False,
    }]
    assert edges == [{
        "src": "custommetadatarecord/Acme_Rate.Standard_Rate",
        "type": "references", "to_kind": "object", "to_name": "Acme_Rate__mdt",
    }]


def test_values_are_never_emitted(tmp_path):
    """The record's <field>/<value> are configuration DATA and must not surface."""
    p = tmp_path / "Acme_Rate.Secret.md-meta.xml"
    _w(p, _record_xml("Secret", "true", "ApiKey__c", "sk-do-not-leak"))
    nodes, edges = CustomMetadataExtractor().extract(p)
    blob = repr(nodes) + repr(edges)
    assert "sk-do-not-leak" not in blob
    assert "ApiKey__c" not in blob
    assert nodes[0]["protected"] is True


def test_malformed_name_skipped(tmp_path):
    """A name without the `<Type>.<Record>` shape emits nothing."""
    p = tmp_path / "NoDotName.md-meta.xml"
    _w(p, "<CustomMetadata/>")
    assert CustomMetadataExtractor().extract(p) == ([], [])


def test_broken_xml_skipped_not_raised(tmp_path):
    p = tmp_path / "Acme_Rate.Broken.md-meta.xml"
    _w(p, "<CustomMetadata><values>")  # truncated
    nodes, edges = CustomMetadataExtractor().extract(p)
    # node + reference still emitted from the filename; no protected attr
    assert nodes[0]["id"] == "custommetadatarecord/Acme_Rate.Broken"
    assert "protected" not in nodes[0]
    assert edges[0]["to_name"] == "Acme_Rate__mdt"


def test_build_resolves_reference_and_stubs_type(tmp_path):
    _w(tmp_path / "MeterPoint_Cfg.Default.md-meta.xml",
       _record_xml("Default", "false", "Threshold__c", "10"))
    g = _build(tmp_path)
    assert {"src": "custommetadatarecord/MeterPoint_Cfg.Default",
            "dst": "object/MeterPoint_Cfg__mdt", "type": "references"} in g["edges"]
    # the type object wasn't in the repo -> external stub
    stub = next(n for n in g["nodes"] if n["id"] == "object/MeterPoint_Cfg__mdt")
    assert stub.get("external") is True
    assert g["errors"] == []
