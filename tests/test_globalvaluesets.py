"""Global Value Set extractor tests.

The node carries the set name only; <customValue> entries are never emitted.
"""
from pathlib import Path

from graphbuilder.extractors.globalvaluesets import EXTRACTORS, GlobalValueSetExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _gvs_xml(label, values):
    body = "".join(
        f"\n  <customValue><fullName>{v}</fullName><label>{v}</label></customValue>"
        for v in values
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<GlobalValueSet {NS}>
  <masterLabel>{label}</masterLabel>{body}
</GlobalValueSet>
"""


def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], GlobalValueSetExtractor)
    assert EXTRACTORS[0].source == "salesforce"


def test_handles_globalvalueset_only():
    ex = GlobalValueSetExtractor()
    assert ex.handles(Path("Region.globalValueSet-meta.xml"))
    assert not ex.handles(Path("Region.object-meta.xml"))


def test_node_is_name_only_no_values(tmp_path):
    p = tmp_path / "Region.globalValueSet-meta.xml"
    _w(p, _gvs_xml("Region", ["EMEA", "APAC", "Americas"]))
    nodes, edges = GlobalValueSetExtractor().extract(p)

    assert nodes == [{"id": "globalvalueset/Region", "type": "globalvalueset", "label": "Region"}]
    assert edges == []
    blob = repr(nodes)
    assert "EMEA" not in blob and "APAC" not in blob


def test_broken_xml_still_emits_name(tmp_path):
    """Name comes from the filename, so a broken body still yields the node."""
    p = tmp_path / "Stage.globalValueSet-meta.xml"
    _w(p, "<GlobalValueSet><customValue>")  # truncated
    nodes, edges = GlobalValueSetExtractor().extract(p)
    assert nodes == [{"id": "globalvalueset/Stage", "type": "globalvalueset", "label": "Stage"}]
    assert edges == []
