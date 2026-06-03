"""FlexiPage extractor tests."""
from graphbuilder import core, resolvers
from graphbuilder.extractors.flexipages import EXTRACTORS, FlexiPageExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _flexipage_xml(sobject, components):
    region = "".join(
        f"""
        <itemInstances>
          <componentInstance>
            <componentName>{c}</componentName>
          </componentInstance>
        </itemInstances>"""
        for c in components
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexiPage {NS}>
  <masterLabel>Acme MeterPoint Record Page</masterLabel>
  <sobjectType>{sobject}</sobjectType>
  <flexiPageRegions>{region}
  </flexiPageRegions>
  <type>RecordPage</type>
</FlexiPage>
"""


def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], FlexiPageExtractor)
    assert EXTRACTORS[0].source == "salesforce"


def test_handles_only_flexipages():
    ex = FlexiPageExtractor()
    assert ex.handles(__import__("pathlib").Path("AcmeMeterPoint.flexipage-meta.xml"))
    assert not ex.handles(__import__("pathlib").Path("AcmeService.cls"))
    assert not ex.handles(__import__("pathlib").Path("AcmeTrigger.trigger"))


def test_extract_nodes_and_edges(tmp_path):
    p = tmp_path / "AcmeMeterPoint.flexipage-meta.xml"
    # two custom components (c:...) plus one standard component that is skipped
    _w(p, _flexipage_xml(
        "MeterPoint__c",
        ["c:meterPointDetail", "c:acmeUsageChart", "flexipage:availableForAllPageTypes"],
    ))
    nodes, edges = FlexiPageExtractor().extract(p)

    # flexipage node
    assert nodes == [{"id": "flexipage/AcmeMeterPoint",
                      "type": "flexipage", "label": "AcmeMeterPoint"}]

    # page-for -> object
    assert {"src": "flexipage/AcmeMeterPoint", "type": "page-for",
            "to_kind": "object", "to_name": "MeterPoint__c"} in edges

    # embeds -> lwc (only the c: custom components, standard one skipped)
    embeds = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("embeds", "lwc", "meterPointDetail") in embeds
    assert ("embeds", "lwc", "acmeUsageChart") in embeds
    assert all(tk == "lwc" or tn != "availableForAllPageTypes"
               for _, tk, tn in embeds)

    # exactly: 1 page-for + 2 embeds
    assert len(edges) == 3


def test_extract_no_object_no_page_for_edge(tmp_path):
    # App/Home pages have no <sobjectType> — must not emit a page-for edge
    p = tmp_path / "AcmeHome.flexipage-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexiPage {NS}>
  <masterLabel>Acme Home</masterLabel>
  <type>HomePage</type>
  <flexiPageRegions>
    <itemInstances>
      <componentInstance><componentName>c:acmeHomeBanner</componentName></componentInstance>
    </itemInstances>
  </flexiPageRegions>
</FlexiPage>
""")
    nodes, edges = FlexiPageExtractor().extract(p)
    assert nodes[0]["id"] == "flexipage/AcmeHome"
    assert all(e["type"] != "page-for" for e in edges)
    assert {"src": "flexipage/AcmeHome", "type": "embeds",
            "to_kind": "lwc", "to_name": "acmeHomeBanner"} in edges


def test_broken_xml_is_skipped_not_raised(tmp_path):
    # malformed XML must not raise — just a bare flexipage node, no edges
    p = tmp_path / "AcmeBroken.flexipage-meta.xml"
    _w(p, f"<FlexiPage {NS}><sobjectType>Acme__c</sobjectType")  # unterminated
    nodes, edges = FlexiPageExtractor().extract(p)
    assert nodes[0]["id"] == "flexipage/AcmeBroken"
    assert edges == []


def test_build_graph_in_isolation(tmp_path):
    p = tmp_path / "AcmeMeterPoint.flexipage-meta.xml"
    _w(p, _flexipage_xml("MeterPoint__c", ["c:meterPointDetail"]))

    g = (core.GraphBuilder()
         .register(FlexiPageExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    ids = {n["id"]: n for n in g["nodes"]}
    assert "flexipage/AcmeMeterPoint" in ids
    # page-for target is a standard-or-absent object -> resolves to an external stub
    assert ids["object/MeterPoint__c"].get("external") is True
    assert ids["lwc/meterPointDetail"].get("external") is True
    assert any(e["type"] == "page-for" and e["src"] == "flexipage/AcmeMeterPoint"
               and e["dst"] == "object/MeterPoint__c" for e in g["edges"])
    assert any(e["type"] == "embeds" and e["dst"] == "lwc/meterPointDetail"
               for e in g["edges"])
    assert g["errors"] == [] and g["unresolved"] == []
