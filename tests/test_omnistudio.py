"""OmniStudio extractor tests."""
from graphbuilder.extractors.omnistudio import OmniStudioExtractor, EXTRACTORS
from graphbuilder.core import GraphBuilder
from graphbuilder.resolvers import default_resolvers


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


# A standard OmniScript *-meta.xml. References live in the embedded JSON of
# <propertySetConfig>.
def _omniscript_xml(version, active=True, *, ip="Acme_LookupMeterPoint",
                    dm="AcmeMeterPointExtract", apex="AcmeMeterPointController",
                    lwc="acmeMeterPointCard"):
    psc = {
        "elements": [
            {"integrationProcedureKey": ip},
            {"bundle": dm},
            {"remoteClass": apex},
            {"lwcComponentName": lwc},
            {"objectName": "MeterPoint__c"},
        ]
    }
    import json as _json
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OmniProcess xmlns="http://soap.sforce.com/2006/04/metadata">'
        "<type>Acme</type><subType>MeterPointFlow</subType>"
        f"<name>Acme_MeterPointFlow_{version}</name>"
        f"<isActive>{'true' if active else 'false'}</isActive>"
        f"<versionNumber>{version}</versionNumber>"
        "<propertySetConfig>" + _json.dumps(psc) + "</propertySetConfig>"
        "</OmniProcess>"
    )


def _datamapper_xml(name="AcmeMeterPointExtract"):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OmniDataTransform xmlns="http://soap.sforce.com/2006/04/metadata">'
        f"<name>{name}</name><isActive>true</isActive><versionNumber>1</versionNumber>"
        "<inputObjectName>MeterPoint__c</inputObjectName>"
        "<outputObjectName>JSON</outputObjectName>"
        "</OmniDataTransform>"
    )


def test_handles():
    ex = OmniStudioExtractor()
    from pathlib import Path
    assert ex.handles(Path("a/X.os-meta.xml"))
    assert ex.handles(Path("a/X.oip-meta.xml"))
    assert ex.handles(Path("a/X.rpt-meta.xml"))
    assert ex.handles(Path("a/X.ouc-meta.xml"))
    assert ex.handles(Path("a/Acme_Thing_DataPack.json"))
    assert not ex.handles(Path("a/Acme.cls"))
    assert not ex.handles(Path("a/Acme.flow-meta.xml"))


def test_registry_exposed():
    assert EXTRACTORS and isinstance(EXTRACTORS[0], OmniStudioExtractor)


def test_omniscript_nodes_and_edges(tmp_path):
    p = tmp_path / "omni" / "Acme_MeterPointFlow_2.os-meta.xml"
    _w(p, _omniscript_xml(2, active=True))
    nodes, edges = OmniStudioExtractor().extract(p)

    assert len(nodes) == 1
    n = nodes[0]
    assert n["id"] == "omniscript/Acme_MeterPointFlow"   # canonical Type_SubType
    assert n["type"] == "omniscript"

    def has(etype, kind, name):
        return any(e["src"] == "omniscript/Acme_MeterPointFlow" and e["type"] == etype
                   and e["to_kind"] == kind and e["to_name"] == name for e in edges)

    assert has("calls", "integrationprocedure", "Acme_LookupMeterPoint")
    assert has("uses", "datamapper", "AcmeMeterPointExtract")
    assert has("calls", "apexclass", "AcmeMeterPointController")
    assert has("embeds", "lwc", "acmeMeterPointCard")
    assert has("touches", "object", "MeterPoint__c")     # non-datamapper -> touches


def test_datamapper_maps_object(tmp_path):
    p = tmp_path / "dm" / "AcmeMeterPointExtract.rpt-meta.xml"
    _w(p, _datamapper_xml())
    nodes, edges = OmniStudioExtractor().extract(p)
    assert nodes[0]["id"] == "datamapper/AcmeMeterPointExtract"
    assert nodes[0]["type"] == "datamapper"
    # datamapper uses 'maps' (not 'touches'); JSON output format is dropped
    assert any(e["type"] == "maps" and e["to_kind"] == "object"
               and e["to_name"] == "MeterPoint__c" for e in edges)
    assert not any(e["to_name"].lower() == "json" for e in edges)


def test_only_active_or_highest_version_emitted(tmp_path):
    """Three version files for one canonical OmniScript; only the active wins."""
    d = tmp_path / "omni"
    _w(d / "Acme_MeterPointFlow_1.os-meta.xml", _omniscript_xml(1, active=False))
    _w(d / "Acme_MeterPointFlow_2.os-meta.xml", _omniscript_xml(2, active=False))
    _w(d / "Acme_MeterPointFlow_3.os-meta.xml", _omniscript_xml(3, active=True))

    ex = OmniStudioExtractor()
    emitted = []
    for f in sorted(d.glob("*.os-meta.xml")):
        nodes, _ = ex.extract(f)
        emitted.extend(nodes)

    # exactly one node for the canonical component, and it's the active version (3)
    omni = [n for n in emitted if n["id"] == "omniscript/Acme_MeterPointFlow"]
    assert len(omni) == 1
    assert omni[0]["version"] == 3.0
    assert omni[0]["active"] is True


def test_highest_version_when_none_active(tmp_path):
    d = tmp_path / "omni"
    _w(d / "Acme_MeterPointFlow_1.os-meta.xml", _omniscript_xml(1, active=False))
    _w(d / "Acme_MeterPointFlow_4.os-meta.xml", _omniscript_xml(4, active=False))

    ex = OmniStudioExtractor()
    emitted = []
    for f in sorted(d.glob("*.os-meta.xml")):
        nodes, _ = ex.extract(f)
        emitted.extend(nodes)
    omni = [n for n in emitted if n["id"] == "omniscript/Acme_MeterPointFlow"]
    assert len(omni) == 1
    assert omni[0]["version"] == 4.0


def test_broken_xml_does_not_raise(tmp_path):
    p = tmp_path / "omni" / "Acme_Broken_1.os-meta.xml"
    _w(p, "<OmniProcess><type>Acme</type><subType>Broken")  # truncated / invalid
    nodes, edges = OmniStudioExtractor().extract(p)          # must not raise
    assert isinstance(nodes, list) and isinstance(edges, list)


def test_datapack_vlocity(tmp_path):
    import json
    dp = {
        "name": "Acme_LegacyExtract",
        "VlocityRecordSObjectType": "DataRaptor",
        "items": [
            {"bundle": "AcmeLegacyMapper"},
            {"objectName": "MeterPoint__c"},
        ],
    }
    p = tmp_path / "datapacks" / "Acme_LegacyExtract_DataPack.json"
    _w(p, json.dumps(dp))
    nodes, edges = OmniStudioExtractor().extract(p)
    assert len(nodes) == 1
    assert nodes[0]["id"] == "datamapper/Acme_LegacyExtract"
    assert nodes[0]["type"] == "datamapper"
    # datamapper -> object via 'maps'
    assert any(e["type"] == "maps" and e["to_name"] == "MeterPoint__c" for e in edges)
    assert any(e["type"] == "uses" and e["to_kind"] == "datamapper"
               and e["to_name"] == "AcmeLegacyMapper" for e in edges)


def test_graph_build_isolated_resolves_to_stubs(tmp_path):
    """Referenced targets not in the repo resolve to external stubs."""
    p = tmp_path / "omni" / "Acme_MeterPointFlow_1.os-meta.xml"
    _w(p, _omniscript_xml(1, active=True))
    g = (GraphBuilder()
         .register(OmniStudioExtractor())
         .register_resolver(*default_resolvers())
         .build(tmp_path))
    ids = {n["id"]: n for n in g["nodes"]}
    assert "omniscript/Acme_MeterPointFlow" in ids
    # referenced targets not in the repo become external stubs
    assert ids["integrationprocedure/Acme_LookupMeterPoint"].get("external") is True
    assert ids["datamapper/AcmeMeterPointExtract"].get("external") is True
    assert ids["apexclass/AcmeMeterPointController"].get("external") is True
    assert ids["lwc/acmeMeterPointCard"].get("external") is True
    assert ids["object/MeterPoint__c"].get("external") is True
    assert g["errors"] == [] and g["unresolved"] == []
    assert any(e["type"] == "calls" and e["dst"] == "integrationprocedure/Acme_LookupMeterPoint"
               for e in g["edges"])


# Element-level fidelity: nested omniProcessElements / items.
import json as _json


def _omniscript_with_elements_xml(version=1, active=True):
    """An OmniScript whose nested <omniProcessElements> carry per-element actions:
    a Remote (Apex) action, an Integration Procedure action, a DataRaptor action."""
    def element(name, etype, psc):
        return (
            "<omniProcessElements>"
            f"<name>{name}</name><type>{etype}</type>"
            "<propertySetConfig>" + _json.dumps(psc) + "</propertySetConfig>"
            "</omniProcessElements>"
        )
    elems = (
        element("DoRemote", "Remote Action", {"remoteClass": "AcmeRemoteController"})
        + element("CallIP", "Integration Procedure Action",
                  {"integrationProcedureKey": "Acme_SubProcedure"})
        + element("ExtractData", "DataRaptor Extract Action",
                  {"bundle": "AcmeExtractMapper"})
    )
    # script-level propertySetConfig keeps the existing flat refs alive too
    top_psc = {"elements": [{"objectName": "MeterPoint__c"}]}
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OmniProcess xmlns="http://soap.sforce.com/2006/04/metadata">'
        "<type>Acme</type><subType>MeterPointFlow</subType>"
        f"<name>Acme_MeterPointFlow_{version}</name>"
        f"<isActive>{'true' if active else 'false'}</isActive>"
        f"<versionNumber>{version}</versionNumber>"
        "<propertySetConfig>" + _json.dumps(top_psc) + "</propertySetConfig>"
        + elems +
        "</OmniProcess>"
    )


def test_element_nodes_and_contains(tmp_path):
    p = tmp_path / "omni" / "Acme_MeterPointFlow_1.os-meta.xml"
    _w(p, _omniscript_with_elements_xml())
    nodes, edges = OmniStudioExtractor().extract(p)

    oid = "omniscript/Acme_MeterPointFlow"
    eids = {n["id"] for n in nodes if n["type"] == "flowelement"}
    assert "flowelement/Acme_MeterPointFlow.DoRemote" in eids
    assert "flowelement/Acme_MeterPointFlow.CallIP" in eids
    assert "flowelement/Acme_MeterPointFlow.ExtractData" in eids

    # every element is contained by the component
    for elem in ("DoRemote", "CallIP", "ExtractData"):
        assert any(e["src"] == oid and e["type"] == "contains"
                   and e["to_kind"] == "flowelement"
                   and e["to_name"] == f"Acme_MeterPointFlow.{elem}" for e in edges)

    # element type carried as a structural attr (a name, not a value)
    remote = next(n for n in nodes if n["id"] == "flowelement/Acme_MeterPointFlow.DoRemote")
    assert remote["element_type"] == "Remote Action"


def test_element_typed_edges(tmp_path):
    p = tmp_path / "omni" / "Acme_MeterPointFlow_1.os-meta.xml"
    _w(p, _omniscript_with_elements_xml())
    nodes, edges = OmniStudioExtractor().extract(p)

    def has(src, etype, kind, name):
        return any(e["src"] == src and e["type"] == etype
                   and e["to_kind"] == kind and e["to_name"] == name for e in edges)

    # Remote/Apex action -> calls -> apexclass
    assert has("flowelement/Acme_MeterPointFlow.DoRemote",
               "calls", "apexclass", "AcmeRemoteController")
    # Integration Procedure action -> calls -> integrationprocedure
    assert has("flowelement/Acme_MeterPointFlow.CallIP",
               "calls", "integrationprocedure", "Acme_SubProcedure")
    # DataRaptor action -> uses -> datamapper
    assert has("flowelement/Acme_MeterPointFlow.ExtractData",
               "uses", "datamapper", "AcmeExtractMapper")


def test_element_refs_deduped_from_component(tmp_path):
    """A ref configured inside an element is emitted ONLY on that flowelement,
    never duplicated on the component. Script/top-level refs stay on the
    component, and the element ref is still reachable via `contains`."""
    p = tmp_path / "omni" / "Acme_MeterPointFlow_1.os-meta.xml"
    _w(p, _omniscript_with_elements_xml())
    nodes, edges = OmniStudioExtractor().extract(p)
    oid = "omniscript/Acme_MeterPointFlow"
    eid = "flowelement/Acme_MeterPointFlow.DoRemote"

    # the component node still exists exactly once
    assert len([n for n in nodes if n["id"] == oid]) == 1

    # a script/top-level ref (objectName in the component's OWN propertySetConfig)
    # stays directly on the component
    assert any(e["src"] == oid and e["type"] == "touches"
               and e["to_name"] == "MeterPoint__c" for e in edges)

    # the element-internal apex ref lives on its flowelement...
    assert any(e["src"] == eid and e["type"] == "calls" and e["to_kind"] == "apexclass"
               and e["to_name"] == "AcmeRemoteController" for e in edges)
    # ...is NOT duplicated on the component...
    assert not any(e["src"] == oid and e["to_name"] == "AcmeRemoteController"
                   for e in edges)
    # ...but is still reachable: component -contains-> the element that calls it
    assert any(e["src"] == oid and e["type"] == "contains"
               and e["to_name"] == "Acme_MeterPointFlow.DoRemote" for e in edges)

    # none of the element-internal refs leak onto the component
    element_internal = {"AcmeRemoteController", "Acme_SubProcedure", "AcmeExtractMapper"}
    comp_targets = {e["to_name"] for e in edges if e["src"] == oid}
    assert not (element_internal & comp_targets), \
        f"element refs leaked onto the component: {element_internal & comp_targets}"


def _flexcard_with_actions_xml(name="AcmeAccountCard"):
    """A FlexCard whose state element fires a navigate/flip action to a child card."""
    psc = {
        "states": [{
            "components": [{
                "actions": [
                    {"type": "Card", "targetCardName": "AcmeChildCard"},
                    {"type": "Custom LWC", "lwcComponentName": "acmeCustomWidget"},
                ]
            }]
        }]
    }
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OmniUiCard xmlns="http://soap.sforce.com/2006/04/metadata">'
        f"<name>{name}</name><isActive>true</isActive><versionNumber>1</versionNumber>"
        "<omniProcessElements>"
        "<name>HeaderState</name><type>State</type>"
        "<propertySetConfig>" + _json.dumps(psc) + "</propertySetConfig>"
        "</omniProcessElements>"
        "</OmniUiCard>"
    )


def test_flexcard_action_embeds_card(tmp_path):
    p = tmp_path / "fc" / "AcmeAccountCard.ouc-meta.xml"
    _w(p, _flexcard_with_actions_xml())
    nodes, edges = OmniStudioExtractor().extract(p)
    assert nodes[0]["id"] == "flexcard/AcmeAccountCard"
    eid = "flowelement/AcmeAccountCard.HeaderState"
    assert any(n["id"] == eid and n["type"] == "flowelement" for n in nodes)
    # action that targets a child card -> embeds -> flexcard
    assert any(e["src"] == eid and e["type"] == "embeds"
               and e["to_kind"] == "flexcard"
               and e["to_name"] == "AcmeChildCard" for e in edges)
    # action that names a custom LWC -> embeds -> lwc
    assert any(e["src"] == eid and e["type"] == "embeds"
               and e["to_kind"] == "lwc"
               and e["to_name"] == "acmeCustomWidget" for e in edges)


def _datamapper_with_mappings_xml(name="AcmeMeterPointExtract"):
    """A Data Mapper whose element mappings name Object.Field targets."""
    psc = {
        "mappings": [
            {"outputFieldName": "MeterPoint__c.Name"},
            {"targetFieldName": "MeterPoint__c.Status__c"},
        ]
    }
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OmniDataTransform xmlns="http://soap.sforce.com/2006/04/metadata">'
        f"<name>{name}</name><isActive>true</isActive><versionNumber>1</versionNumber>"
        "<inputObjectName>MeterPoint__c</inputObjectName>"
        "<outputObjectName>JSON</outputObjectName>"
        "<omniProcessElements>"
        "<name>Map1</name><type>Field Mapping</type>"
        "<propertySetConfig>" + _json.dumps(psc) + "</propertySetConfig>"
        "</omniProcessElements>"
        "</OmniDataTransform>"
    )


def test_datamapper_field_mappings(tmp_path):
    p = tmp_path / "dm" / "AcmeMeterPointExtract.rpt-meta.xml"
    _w(p, _datamapper_with_mappings_xml())
    nodes, edges = OmniStudioExtractor().extract(p)
    assert nodes[0]["id"] == "datamapper/AcmeMeterPointExtract"
    eid = "flowelement/AcmeMeterPointExtract.Map1"
    # field mappings -> maps -> field (Object.Field)
    assert any(e["src"] == eid and e["type"] == "maps" and e["to_kind"] == "field"
               and e["to_name"] == "MeterPoint__c.Name" for e in edges)
    assert any(e["src"] == eid and e["type"] == "maps" and e["to_kind"] == "field"
               and e["to_name"] == "MeterPoint__c.Status__c" for e in edges)
    # and -> object (owning object of the mapped field)
    assert any(e["src"] == eid and e["type"] == "maps" and e["to_kind"] == "object"
               and e["to_name"] == "MeterPoint__c" for e in edges)
    # base behavior intact: JSON output format never becomes an object
    assert not any(e["to_name"].lower() == "json" for e in edges)


def test_datapack_element_actions(tmp_path):
    """Vlocity DataPack: per-item actions still yield element nodes + typed edges."""
    dp = {
        "name": "Acme_LegacyFlow",
        "omniProcessType": "OmniScript",
        "items": [
            {"name": "RemoteStep", "type": "Remote Action",
             "remoteClass": "AcmeLegacyController"},
            {"name": "IPStep", "type": "Integration Procedure Action",
             "integrationProcedureKey": "Acme_LegacySub"},
        ],
    }
    p = tmp_path / "datapacks" / "Acme_LegacyFlow_DataPack.json"
    _w(p, _json.dumps(dp))
    nodes, edges = OmniStudioExtractor().extract(p)
    oid = nodes[0]["id"]
    assert any(n["id"] == "flowelement/Acme_LegacyFlow.RemoteStep" for n in nodes)
    assert any(e["src"] == oid and e["type"] == "contains"
               and e["to_name"] == "Acme_LegacyFlow.RemoteStep" for e in edges)
    assert any(e["src"] == "flowelement/Acme_LegacyFlow.RemoteStep"
               and e["type"] == "calls" and e["to_kind"] == "apexclass"
               and e["to_name"] == "AcmeLegacyController" for e in edges)
    assert any(e["src"] == "flowelement/Acme_LegacyFlow.IPStep"
               and e["type"] == "calls" and e["to_kind"] == "integrationprocedure"
               and e["to_name"] == "Acme_LegacySub" for e in edges)


def test_element_layer_graph_build_isolated(tmp_path):
    """Element nodes/edges resolve cleanly; targets become external stubs."""
    p = tmp_path / "omni" / "Acme_MeterPointFlow_1.os-meta.xml"
    _w(p, _omniscript_with_elements_xml())
    g = (GraphBuilder()
         .register(OmniStudioExtractor())
         .register_resolver(*default_resolvers())
         .build(tmp_path))
    ids = {n["id"]: n for n in g["nodes"]}
    assert "flowelement/Acme_MeterPointFlow.DoRemote" in ids
    # contains edge to the element resolves
    assert any(e["type"] == "contains"
               and e["dst"] == "flowelement/Acme_MeterPointFlow.DoRemote"
               for e in g["edges"])
    # per-element calls edge resolves to an external apex stub
    assert any(e["type"] == "calls"
               and e["src"] == "flowelement/Acme_MeterPointFlow.DoRemote"
               and e["dst"] == "apexclass/AcmeRemoteController" for e in g["edges"])
    assert g["errors"] == [] and g["unresolved"] == []


def test_broken_elements_do_not_raise(tmp_path):
    """Truncated element JSON must be skipped, never raised."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OmniProcess xmlns="http://soap.sforce.com/2006/04/metadata">'
        "<type>Acme</type><subType>Broken</subType>"
        "<name>Acme_Broken_1</name><isActive>true</isActive><versionNumber>1</versionNumber>"
        "<omniProcessElements><name>BadStep</name><type>Remote Action</type>"
        "<propertySetConfig>{not valid json</propertySetConfig>"
        "</omniProcessElements>"
        "</OmniProcess>"
    )
    p = tmp_path / "omni" / "Acme_Broken_1.os-meta.xml"
    _w(p, xml)
    nodes, edges = OmniStudioExtractor().extract(p)   # must not raise
    assert isinstance(nodes, list) and isinstance(edges, list)
    # element node still emitted even though its JSON was unparseable
    assert any(n["id"] == "flowelement/Acme_Broken.BadStep" for n in nodes)
