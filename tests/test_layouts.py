"""Page-layout extractor tests.

`reads`->field and `uses`->quickaction targets are owned by other extractors, so
those are asserted from `extract()` output rather than from resolution.
"""
from pathlib import Path

from graphbuilder import core, resolvers
from graphbuilder.extractors.layouts import EXTRACTORS, LayoutExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _layout_xml(fields, quick_actions=(), platform_actions=()):
    items = "".join(
        f"""
        <layoutItems>
          <behavior>Edit</behavior>
          <field>{f}</field>
        </layoutItems>"""
        for f in fields
    )
    quick = "".join(
        f"""
      <quickActionListItems>
        <quickActionName>{q}</quickActionName>
      </quickActionListItems>"""
        for q in quick_actions
    )
    quick_block = f"""
    <quickActionList>{quick}
    </quickActionList>""" if quick_actions else ""
    plat = "".join(
        f"""
      <platformActionListItems>
        <actionName>{a}</actionName>
        <actionType>QuickAction</actionType>
      </platformActionListItems>"""
        for a in platform_actions
    )
    plat_block = f"""
  <platformActionList>
    <actionListContext>Record</actionListContext>{plat}
  </platformActionList>""" if platform_actions else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Layout {NS}>
  <layoutSections>
    <editHeading>true</editHeading>
    <label>Information</label>
    <layoutColumns>{items}
    </layoutColumns>
  </layoutSections>{quick_block}{plat_block}
</Layout>
"""


# registry / handles
def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], LayoutExtractor)
    assert EXTRACTORS[0].source == "salesforce"


def test_handles_only_layouts():
    ex = LayoutExtractor()
    assert ex.handles(Path("MeterPoint__c-Acme Layout.layout-meta.xml"))
    assert ex.handles(Path("Account-Acme Compact.compactLayout-meta.xml"))
    assert not ex.handles(Path("AcmeMeterPoint.flexipage-meta.xml"))
    assert not ex.handles(Path("AcmeService.cls"))
    assert not ex.handles(Path("AcmeTrigger.trigger"))


# node + page-for
def test_layout_node_and_page_for(tmp_path):
    p = tmp_path / "MeterPoint__c-Acme MeterPoint Layout.layout-meta.xml"
    _w(p, _layout_xml(["Name", "Status__c"]))
    nodes, edges = LayoutExtractor().extract(p)

    # node id uses the full filename stem (incl. the layout-name dashes/spaces)
    assert nodes == [{
        "id": "layout/MeterPoint__c-Acme MeterPoint Layout",
        "type": "layout",
        "label": "MeterPoint__c-Acme MeterPoint Layout",
    }]

    # page-for -> object : object is the prefix before the FIRST dash
    assert {"src": "layout/MeterPoint__c-Acme MeterPoint Layout",
            "type": "page-for", "to_kind": "object",
            "to_name": "MeterPoint__c"} in edges


def test_object_prefix_only_before_first_dash(tmp_path):
    # a layout name that itself contains dashes must not bleed into the object
    p = tmp_path / "Account-Sales - Rep - View.layout-meta.xml"
    _w(p, _layout_xml(["Name"]))
    nodes, edges = LayoutExtractor().extract(p)
    page_for = [e for e in edges if e["type"] == "page-for"]
    assert page_for == [{"src": "layout/Account-Sales - Rep - View",
                         "type": "page-for", "to_kind": "object",
                         "to_name": "Account"}]


# reads -> field (Object.Field), names only
def test_reads_fields_qualified_with_object(tmp_path):
    p = tmp_path / "MeterPoint__c-Layout.layout-meta.xml"
    _w(p, _layout_xml(["Name", "Status__c", "Globex_Reading__c"]))
    _, edges = LayoutExtractor().extract(p)
    reads = {(e["to_kind"], e["to_name"]) for e in edges if e["type"] == "reads"}
    assert ("field", "MeterPoint__c.Name") in reads
    assert ("field", "MeterPoint__c.Status__c") in reads
    assert ("field", "MeterPoint__c.Globex_Reading__c") in reads
    assert len(reads) == 3


def test_reads_fields_are_deduped(tmp_path):
    p = tmp_path / "Acme__c-Layout.layout-meta.xml"
    _w(p, _layout_xml(["Name", "Name", "Total__c"]))
    _, edges = LayoutExtractor().extract(p)
    reads = [e for e in edges if e["type"] == "reads"]
    names = sorted(e["to_name"] for e in reads)
    assert names == ["Acme__c.Name", "Acme__c.Total__c"]


# uses -> quickaction, name only
def test_uses_quickactions_name_only(tmp_path):
    p = tmp_path / "MeterPoint__c-Layout.layout-meta.xml"
    _w(p, _layout_xml(
        ["Name"],
        quick_actions=["LogReading", "Account.NewContact"],
        platform_actions=["NewTask", "LogReading"],  # LogReading shared -> deduped
    ))
    _, edges = LayoutExtractor().extract(p)
    qa = {e["to_name"] for e in edges if e["type"] == "uses"
          and e["to_kind"] == "quickaction"}
    assert qa == {"LogReading", "Account.NewContact", "NewTask"}
    # each emitted exactly once
    qa_edges = [e for e in edges if e["type"] == "uses"]
    assert len(qa_edges) == 3


def test_no_field_values_emitted(tmp_path):
    # edges carry names only; no value-ish payloads slip in
    p = tmp_path / "Globex__c-Layout.layout-meta.xml"
    _w(p, _layout_xml(["Amount__c"], quick_actions=["DoThing"]))
    _, edges = LayoutExtractor().extract(p)
    for e in edges:
        assert set(e.keys()) == {"src", "type", "to_kind", "to_name"}
        # field edges are Object.Field; nothing carries a 'behavior'/label/value
        assert "behavior" not in e
        assert "label" not in e


# robustness
def test_broken_xml_keeps_node_and_page_for(tmp_path):
    # malformed XML must not raise; page-for is derivable from the filename alone
    p = tmp_path / "Acme__c-Broken.layout-meta.xml"
    _w(p, f"<Layout {NS}><layoutSections><field>Name</field")  # unterminated
    nodes, edges = LayoutExtractor().extract(p)
    assert nodes[0]["id"] == "layout/Acme__c-Broken"
    assert {"src": "layout/Acme__c-Broken", "type": "page-for",
            "to_kind": "object", "to_name": "Acme__c"} in edges
    # no field/quickaction edges from the unparseable body
    assert all(e["type"] == "page-for" for e in edges)


def test_compact_layout_handled_same_way(tmp_path):
    p = tmp_path / "MeterPoint__c-Acme Compact.compactLayout-meta.xml"
    _w(p, """<?xml version="1.0" encoding="UTF-8"?>
<CompactLayout %s>
  <fields>Name</fields>
  <fields>Status__c</fields>
  <label>Acme Compact</label>
</CompactLayout>
""" % NS)
    nodes, edges = LayoutExtractor().extract(p)
    # stem strips the .compactLayout-meta.xml suffix
    assert nodes[0]["id"] == "layout/MeterPoint__c-Acme Compact"
    # compact layouts use <fields> (not <field>), so no reads here — but page-for holds
    assert {"src": "layout/MeterPoint__c-Acme Compact", "type": "page-for",
            "to_kind": "object", "to_name": "MeterPoint__c"} in edges


# build in isolation
def test_build_graph_in_isolation(tmp_path):
    p = tmp_path / "MeterPoint__c-Acme Layout.layout-meta.xml"
    _w(p, _layout_xml(["Name", "Status__c"],
                      quick_actions=["LogReading"]))

    g = (core.GraphBuilder()
         .register(LayoutExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    ids = {n["id"]: n for n in g["nodes"]}
    assert "layout/MeterPoint__c-Acme Layout" in ids

    # page-for resolves to an external object stub
    assert ids["object/MeterPoint__c"].get("external") is True
    assert any(e["type"] == "page-for"
               and e["src"] == "layout/MeterPoint__c-Acme Layout"
               and e["dst"] == "object/MeterPoint__c" for e in g["edges"])

    # reads -> field resolves to an external field stub (field owned by objects.py)
    assert ids["field/MeterPoint__c.Name"].get("external") is True
    assert any(e["type"] == "reads"
               and e["dst"] == "field/MeterPoint__c.Name" for e in g["edges"])

    # `quickaction` is now a default stub kind -> the uses edge resolves to an
    # external quickaction stub.
    assert ids["quickaction/LogReading"].get("external") is True
    assert any(e["type"] == "uses" and e["dst"] == "quickaction/LogReading"
               for e in g["edges"])

    assert g["errors"] == []


def test_build_graph_no_errors_on_broken_file(tmp_path):
    p = tmp_path / "Acme__c-Broken.layout-meta.xml"
    _w(p, f"<Layout {NS}><layoutSections><field>Name</field")
    g = (core.GraphBuilder()
         .register(LayoutExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    assert g["errors"] == []
    assert "layout/Acme__c-Broken" in {n["id"] for n in g["nodes"]}
