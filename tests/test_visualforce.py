"""Visualforce extractor tests (.page / .component)."""
from pathlib import Path

from graphbuilder import core, resolvers
from graphbuilder.extractors.visualforce import EXTRACTORS, VisualforceExtractor


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _edge_tuples(edges):
    return {(e["type"], e["to_kind"], e["to_name"]) for e in edges}


# --------------------------------------------------------------------------- #
# registry / handles
# --------------------------------------------------------------------------- #
def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], VisualforceExtractor)
    assert EXTRACTORS[0].source == "salesforce"


def test_handles_pages_and_components_only():
    ex = VisualforceExtractor()
    assert ex.handles(Path("AcmeMeterPoint.page"))
    assert ex.handles(Path("AcmeUsagePanel.component"))
    # config sidecars / unrelated metadata are NOT owned here
    assert not ex.handles(Path("AcmeMeterPoint.page-meta.xml"))
    assert not ex.handles(Path("AcmeUsagePanel.component-meta.xml"))
    assert not ex.handles(Path("AcmeService.cls"))
    assert not ex.handles(Path("AcmeTrigger.trigger"))


# --------------------------------------------------------------------------- #
# page: node + standardController + extensions + custom component tags
# --------------------------------------------------------------------------- #
def test_page_node_references_calls_and_uses_component(tmp_path):
    p = tmp_path / "AcmeMeterPoint.page"
    _w(p, """<apex:page standardController="MeterPoint__c"
                        extensions="AcmeMeterPointExt, Globex.UsageExt"
                        showHeader="false">
  <h1>Meter readings for {!meterPoint.Name}</h1>
  <c:meterPointPanel value="{!current}"/>
  <c:AcmeUsageChart period="monthly">body text here</c:AcmeUsageChart>
  <apex:pageBlock title="Secret subject line not a class">
    <apex:outputText value="https://internal.example.com/secret"/>
  </apex:pageBlock>
</apex:page>
""")
    nodes, edges = VisualforceExtractor().extract(p)

    assert nodes == [{"id": "vfpage/AcmeMeterPoint",
                      "type": "vfpage", "label": "AcmeMeterPoint"}]

    tuples = _edge_tuples(edges)
    # standardController -> references -> object
    assert ("references", "object", "MeterPoint__c") in tuples
    # extensions -> calls -> apexclass (comma split, namespace dropped)
    assert ("calls", "apexclass", "AcmeMeterPointExt") in tuples
    assert ("calls", "apexclass", "UsageExt") in tuples
    # custom <c:...> tags -> uses-component -> vfcomponent
    assert ("uses-component", "vfcomponent", "meterPointPanel") in tuples
    assert ("uses-component", "vfcomponent", "AcmeUsageChart") in tuples

    # exactly: 1 references + 2 calls + 2 uses-component
    assert len(edges) == 5
    assert all(e["src"] == "vfpage/AcmeMeterPoint" for e in edges)


def test_page_controller_attribute_is_apexclass(tmp_path):
    p = tmp_path / "AcmeStandalone.page"
    _w(p, '<apex:page controller="AcmeStandaloneCtrl">hello</apex:page>')
    _, edges = VisualforceExtractor().extract(p)
    assert _edge_tuples(edges) == {("calls", "apexclass", "AcmeStandaloneCtrl")}


# --------------------------------------------------------------------------- #
# component: node kind + controller, no standardController expected
# --------------------------------------------------------------------------- #
def test_component_node_and_controller(tmp_path):
    p = tmp_path / "AcmeUsagePanel.component"
    _w(p, """<apex:component controller="AcmeUsagePanelCtrl">
  <c:globexBadge/>
  <apex:attribute name="meter" type="MeterPoint__c" description="ignored text"/>
</apex:component>
""")
    nodes, edges = VisualforceExtractor().extract(p)

    assert nodes == [{"id": "vfcomponent/AcmeUsagePanel",
                      "type": "vfcomponent", "label": "AcmeUsagePanel"}]
    tuples = _edge_tuples(edges)
    assert ("calls", "apexclass", "AcmeUsagePanelCtrl") in tuples
    assert ("uses-component", "vfcomponent", "globexBadge") in tuples
    # `type="MeterPoint__c"` on apex:attribute is NOT a standardController -> no references
    assert not any(e["type"] == "references" for e in edges)
    assert len(edges) == 2


# --------------------------------------------------------------------------- #
# confidentiality: only names/structure leave; no body/value/url text
# --------------------------------------------------------------------------- #
def test_no_body_or_value_text_leaks(tmp_path):
    p = tmp_path / "AcmeSecret.page"
    secret_url = "https://internal.example.com/very-secret-endpoint"
    _w(p, f"""<apex:page standardController="MeterPoint__c" controller="AcmeCtrl">
  <apex:outputLink value="{secret_url}">Confidential subject heading</apex:outputLink>
  <p>Body paragraph with sensitive 12345 numbers and a password=hunter2 token.</p>
  <c:acmePanel/>
</apex:page>
""")
    nodes, edges = VisualforceExtractor().extract(p)
    blob = repr(nodes) + repr(edges)
    for leaked in ("internal.example.com", "very-secret-endpoint",
                   "Confidential", "hunter2", "password", "12345",
                   "Body paragraph", "outputLink"):
        assert leaked not in blob, f"leaked content: {leaked!r}"
    # only the intended structural refs are present
    assert _edge_tuples(edges) == {
        ("references", "object", "MeterPoint__c"),
        ("calls", "apexclass", "AcmeCtrl"),
        ("uses-component", "vfcomponent", "acmePanel"),
    }


# --------------------------------------------------------------------------- #
# robustness: malformed / odd input is skipped, never raised
# --------------------------------------------------------------------------- #
def test_malformed_markup_does_not_raise(tmp_path):
    p = tmp_path / "AcmeBroken.page"
    # well-formed root opener, then a messy/unterminated body that is NOT XML
    _w(p, '<apex:page standardController="MeterPoint__c">'
          '<c:acmePanel/> oops &amp; <<< stray < not a tag <c:halfTag attr=')
    nodes, edges = VisualforceExtractor().extract(p)
    assert nodes[0]["id"] == "vfpage/AcmeBroken"
    # the well-formed openers still yield their edges; nothing raises on the junk
    tuples = _edge_tuples(edges)
    assert ("references", "object", "MeterPoint__c") in tuples
    assert ("uses-component", "vfcomponent", "acmePanel") in tuples


def test_duplicate_refs_collapse(tmp_path):
    p = tmp_path / "AcmeDup.page"
    _w(p, """<apex:page controller="AcmeCtrl">
  <c:acmePanel/>
  <c:acmePanel/>
  <c:acmePanel mode="x"/>
</apex:page>
""")
    _, edges = VisualforceExtractor().extract(p)
    comp = [e for e in edges if e["type"] == "uses-component"]
    assert len(comp) == 1
    assert comp[0]["to_name"] == "acmePanel"


def test_empty_file_yields_bare_node(tmp_path):
    p = tmp_path / "AcmeEmpty.page"
    _w(p, "")
    nodes, edges = VisualforceExtractor().extract(p)
    assert nodes == [{"id": "vfpage/AcmeEmpty", "type": "vfpage", "label": "AcmeEmpty"}]
    assert edges == []


# --------------------------------------------------------------------------- #
# resolved build in isolation
# --------------------------------------------------------------------------- #
def test_build_graph_in_isolation(tmp_path):
    page = tmp_path / "AcmeMeterPoint.page"
    _w(page, """<apex:page standardController="MeterPoint__c" extensions="AcmeExt">
  <c:acmePanel/>
</apex:page>
""")

    g = (core.GraphBuilder()
         .register(VisualforceExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    ids = {n["id"]: n for n in g["nodes"]}
    assert "vfpage/AcmeMeterPoint" in ids
    # references/object and calls/apexclass are stub kinds -> resolve to external stubs
    assert ids["object/MeterPoint__c"].get("external") is True
    assert ids["apexclass/AcmeExt"].get("external") is True
    assert any(e["type"] == "references" and e["src"] == "vfpage/AcmeMeterPoint"
               and e["dst"] == "object/MeterPoint__c" for e in g["edges"])
    assert any(e["type"] == "calls" and e["dst"] == "apexclass/AcmeExt"
               for e in g["edges"])
    assert g["errors"] == []

    # `vfcomponent` is a default stub kind -> the uses-component edge resolves to
    # an external vfcomponent stub
    assert ids["vfcomponent/acmePanel"].get("external") is True
    assert any(e["type"] == "uses-component" and e["dst"] == "vfcomponent/acmePanel"
               for e in g["edges"])
