"""Tests for the app and tab extractor."""
from pathlib import Path

from graphbuilder import core, resolvers
from graphbuilder.extractors.apptabs import EXTRACTORS, AppTabExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _app_xml(tabs):
    body = "".join(f"\n  <tabs>{t}</tabs>" for t in tabs)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomApplication {NS}>
  <label>Acme Service Console</label>
  <navType>Standard</navType>{body}
</CustomApplication>
"""


# registry / handles
def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], AppTabExtractor)
    assert EXTRACTORS[0].source == "salesforce"


def test_handles_apps_and_tabs_only():
    ex = AppTabExtractor()
    assert ex.handles(Path("AcmeConsole.app-meta.xml"))
    assert ex.handles(Path("MeterPoint__c.tab-meta.xml"))
    assert not ex.handles(Path("AcmeService.cls"))
    assert not ex.handles(Path("AcmePage.flexipage-meta.xml"))
    assert not ex.handles(Path("AcmeTrigger.trigger"))


# CustomApplication: app node + contains -> tab
def test_app_node_and_contains_tabs(tmp_path):
    p = tmp_path / "AcmeConsole.app-meta.xml"
    _w(p, _app_xml(["MeterPoint__c", "Globex_Orders", "standard-Account"]))
    nodes, edges = AppTabExtractor().extract(p)

    assert nodes == [{"id": "app/AcmeConsole", "type": "app", "label": "AcmeConsole"}]

    contains = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("contains", "tab", "MeterPoint__c") in contains
    assert ("contains", "tab", "Globex_Orders") in contains
    assert ("contains", "tab", "standard-Account") in contains
    assert all(e["src"] == "app/AcmeConsole" for e in edges)
    assert len(edges) == 3


def test_app_dedupes_repeated_tabs(tmp_path):
    p = tmp_path / "AcmeDup.app-meta.xml"
    _w(p, _app_xml(["MeterPoint__c", "MeterPoint__c", "Globex_Orders"]))
    _, edges = AppTabExtractor().extract(p)
    targets = [e["to_name"] for e in edges]
    assert targets.count("MeterPoint__c") == 1
    assert len(edges) == 2


def test_app_with_no_tabs(tmp_path):
    p = tmp_path / "AcmeEmpty.app-meta.xml"
    _w(p, _app_xml([]))
    nodes, edges = AppTabExtractor().extract(p)
    assert nodes[0]["id"] == "app/AcmeEmpty"
    assert edges == []


# CustomTab: tab node + one classifying edge
def test_tab_custom_object_via_sobjecttype(tmp_path):
    p = tmp_path / "AcmeMeter.tab-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}>
  <label>Acme Meters</label>
  <sobjectType>MeterPoint__c</sobjectType>
  <motif>Custom53: Bell</motif>
</CustomTab>
""")
    nodes, edges = AppTabExtractor().extract(p)
    assert nodes == [{"id": "tab/AcmeMeter", "type": "tab", "label": "AcmeMeter"}]
    assert edges == [{"src": "tab/AcmeMeter", "type": "references",
                      "to_kind": "object", "to_name": "MeterPoint__c"}]


def test_tab_custom_object_via_name_suffix(tmp_path):
    # No <sobjectType>; the tab name itself is the object API name (__c)
    p = tmp_path / "MeterPoint__c.tab-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}>
  <label>Meter Points</label>
  <customObject>true</customObject>
  <motif>Custom53: Bell</motif>
</CustomTab>
""")
    _, edges = AppTabExtractor().extract(p)
    assert edges == [{"src": "tab/MeterPoint__c", "type": "references",
                      "to_kind": "object", "to_name": "MeterPoint__c"}]


def test_tab_custom_object_via_name_suffix_non_c(tmp_path):
    # Packaged orgs expose tabs for external objects (__x) and other non-__c
    # custom suffixes; the name-suffix fallback must recognize them too.
    p = tmp_path / "vlocity_cmt__Ext__x.tab-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}>
  <label>External</label>
  <motif>Custom53: Bell</motif>
</CustomTab>
""")
    _, edges = AppTabExtractor().extract(p)
    assert edges == [{"src": "tab/vlocity_cmt__Ext__x", "type": "references",
                      "to_kind": "object", "to_name": "vlocity_cmt__Ext__x"}]


def test_tab_lwc_component(tmp_path):
    p = tmp_path / "AcmeDashboard.tab-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}>
  <label>Acme Dashboard</label>
  <lwcComponent>acmeDashboard</lwcComponent>
  <motif>Custom53: Bell</motif>
</CustomTab>
""")
    _, edges = AppTabExtractor().extract(p)
    assert edges == [{"src": "tab/AcmeDashboard", "type": "embeds",
                      "to_kind": "lwc", "to_name": "acmeDashboard"}]


def test_tab_aura_component(tmp_path):
    p = tmp_path / "GlobexAura.tab-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}>
  <label>Globex Aura</label>
  <auraComponent>globexConsole</auraComponent>
</CustomTab>
""")
    _, edges = AppTabExtractor().extract(p)
    assert edges == [{"src": "tab/GlobexAura", "type": "embeds",
                      "to_kind": "lwc", "to_name": "globexConsole"}]


def test_tab_flexipage(tmp_path):
    p = tmp_path / "AcmeFlexTab.tab-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}>
  <label>Acme Flex Tab</label>
  <flexiPage>AcmeMeterPointPage</flexiPage>
</CustomTab>
""")
    _, edges = AppTabExtractor().extract(p)
    assert edges == [{"src": "tab/AcmeFlexTab", "type": "page-for",
                      "to_kind": "flexipage", "to_name": "AcmeMeterPointPage"}]


def test_tab_sobjecttype_wins_over_name_suffix(tmp_path):
    # Explicit <sobjectType> takes precedence; only ONE classifying edge emitted
    p = tmp_path / "Legacy__c.tab-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}>
  <sobjectType>MeterPoint__c</sobjectType>
</CustomTab>
""")
    _, edges = AppTabExtractor().extract(p)
    assert len(edges) == 1
    assert edges[0]["to_name"] == "MeterPoint__c"


def test_tab_web_or_other_has_no_edge(tmp_path):
    # A Web/URL tab references none of object/lwc/flexipage -> no classifying edge.
    # (We do NOT parse the <url> value — names/structure only.)
    p = tmp_path / "AcmeWebTab.tab-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}>
  <label>Acme Web Tab</label>
  <url>https://example.invalid/secret</url>
  <urlEncodingKey>UTF-8</urlEncodingKey>
</CustomTab>
""")
    nodes, edges = AppTabExtractor().extract(p)
    assert nodes[0]["id"] == "tab/AcmeWebTab"
    assert edges == []


# robustness
def test_broken_app_xml_is_skipped_not_raised(tmp_path):
    p = tmp_path / "AcmeBroken.app-meta.xml"
    _w(p, f"<CustomApplication {NS}><tabs>MeterPoint__c</tabs")  # unterminated
    nodes, edges = AppTabExtractor().extract(p)
    assert nodes[0]["id"] == "app/AcmeBroken"
    assert edges == []


def test_broken_tab_xml_is_skipped_not_raised(tmp_path):
    p = tmp_path / "AcmeBrokenTab.tab-meta.xml"
    _w(p, f"<CustomTab {NS}><sobjectType>MeterPoint__c</sobjectType")  # unterminated
    nodes, edges = AppTabExtractor().extract(p)
    assert nodes[0]["id"] == "tab/AcmeBrokenTab"
    assert edges == []


# build in isolation
def test_build_graph_in_isolation(tmp_path):
    _w(tmp_path / "AcmeConsole.app-meta.xml", _app_xml(["MeterPoint__c", "AcmeDashboard"]))
    _w(tmp_path / "MeterPoint__c.tab-meta.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}><sobjectType>MeterPoint__c</sobjectType></CustomTab>
""")
    _w(tmp_path / "AcmeDashboard.tab-meta.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomTab {NS}><lwcComponent>acmeDashboard</lwcComponent></CustomTab>
""")

    g = (core.GraphBuilder()
         .register(AppTabExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    ids = {n["id"]: n for n in g["nodes"]}
    assert "app/AcmeConsole" in ids
    assert "tab/MeterPoint__c" in ids
    assert "tab/AcmeDashboard" in ids

    # tab -> object / lwc resolve to external stubs (targets not retrieved here)
    assert ids["object/MeterPoint__c"].get("external") is True
    assert ids["lwc/acmeDashboard"].get("external") is True
    assert any(e["type"] == "references" and e["src"] == "tab/MeterPoint__c"
               and e["dst"] == "object/MeterPoint__c" for e in g["edges"])
    assert any(e["type"] == "embeds" and e["src"] == "tab/AcmeDashboard"
               and e["dst"] == "lwc/acmeDashboard" for e in g["edges"])

    # `tab` is a default stub kind, and both tabs exist here -> app `contains` ->
    # tab edges resolve to the real tab nodes (nothing left unresolved).
    assert [u for u in g["unresolved"] if u["to_kind"] == "tab"] == []
    assert {e["dst"] for e in g["edges"]
            if e["type"] == "contains" and e["src"] == "app/AcmeConsole"} \
        == {"tab/MeterPoint__c", "tab/AcmeDashboard"}

    assert g["errors"] == []
