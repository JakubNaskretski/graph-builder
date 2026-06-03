"""Quick Action extractor tests."""
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.quickactions import QuickActionExtractor

EX = QuickActionExtractor()


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


# Object-specific Create action: object comes from the filename, layout lists two
# fields, no <targetObject>.
CREATE_ACTION = """<?xml version="1.0" encoding="UTF-8"?>
<QuickAction xmlns="http://soap.sforce.com/2006/04/metadata">
    <type>Create</type>
    <label>New Reading</label>
    <quickActionLayout>
        <layoutSectionStyle>OneColumn</layoutSectionStyle>
        <quickActionLayoutColumns>
            <quickActionLayoutItems>
                <field>ReadingValue__c</field>
                <uiBehavior>Edit</uiBehavior>
            </quickActionLayoutItems>
            <quickActionLayoutItems>
                <field>ReadDate__c</field>
                <uiBehavior>Edit</uiBehavior>
            </quickActionLayoutItems>
        </quickActionLayoutColumns>
    </quickActionLayout>
</QuickAction>
"""

# LightningComponent global action. Bare name (no dot) -> no object context.
LWC_ACTION = """<?xml version="1.0" encoding="UTF-8"?>
<QuickAction xmlns="http://soap.sforce.com/2006/04/metadata">
    <type>LightningComponent</type>
    <lightningComponent>acmeMeterPanel</lightningComponent>
    <label>Meter Panel</label>
</QuickAction>
"""

# VisualforcePage action with an explicit <targetObject>.
VF_ACTION = """<?xml version="1.0" encoding="UTF-8"?>
<QuickAction xmlns="http://soap.sforce.com/2006/04/metadata">
    <type>VisualforcePage</type>
    <page>AcmeMeterDetail</page>
    <targetObject>MeterPoint__c</targetObject>
    <label>Detail</label>
</QuickAction>
"""

# Flow action.
FLOW_ACTION = """<?xml version="1.0" encoding="UTF-8"?>
<QuickAction xmlns="http://soap.sforce.com/2006/04/metadata">
    <type>Flow</type>
    <flowDefinition>Acme_Onboard_Meter</flowDefinition>
    <label>Onboard</label>
</QuickAction>
"""


def test_handles():
    assert EX.handles(Path("x/MeterPoint__c.NewReading.quickAction-meta.xml")) is True
    assert EX.handles(Path("x/AcmeGlobal.quickAction-meta.xml")) is True
    assert EX.handles(Path("x/MeterPoint__c.object-meta.xml")) is False
    assert EX.handles(Path("x/AcmeFoo.cls")) is False


def test_node_and_object_context_from_filename():
    """Object-specific action: node id from the full stem, object context from the
    filename segment (no <targetObject>)."""
    f = _w(Path("/tmp/_acme_qa/MeterPoint__c.NewReading.quickAction-meta.xml"),
           CREATE_ACTION)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)

    assert "quickaction/MeterPoint__c.NewReading" in ids
    n = ids["quickaction/MeterPoint__c.NewReading"]
    assert n["type"] == "quickaction"
    assert n["action_type"] == "Create"

    assert ("quickaction/MeterPoint__c.NewReading", "on", "object",
            "MeterPoint__c") in _et(edges)


def test_layout_fields_read_qualified():
    """Layout <field> entries become reads edges, qualified by the object context."""
    f = _w(Path("/tmp/_acme_qa/MeterPoint__c.NewReading.quickAction-meta.xml"),
           CREATE_ACTION)
    _, edges = EX.extract(f)
    et = _et(edges)

    assert ("quickaction/MeterPoint__c.NewReading", "reads", "field",
            "MeterPoint__c.ReadingValue__c") in et
    assert ("quickaction/MeterPoint__c.NewReading", "reads", "field",
            "MeterPoint__c.ReadDate__c") in et


def test_lightning_component_embeds_lwc():
    """LightningComponent: <lightningComponent> -> embeds -> lwc. A bare global
    name has no object context, so no `on` edge."""
    f = _w(Path("/tmp/_acme_qa/AcmeMeterPanel.quickAction-meta.xml"), LWC_ACTION)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)
    et = _et(edges)

    assert "quickaction/AcmeMeterPanel" in ids
    assert ("quickaction/AcmeMeterPanel", "embeds", "lwc", "acmeMeterPanel") in et
    assert not any(e["type"] == "on" for e in edges)


def test_visualforce_embeds_vfpage_and_target_object():
    """VisualforcePage: <page> -> embeds -> vfpage; explicit <targetObject> drives
    the `on` edge."""
    f = _w(Path("/tmp/_acme_qa/MeterPoint__c.Detail.quickAction-meta.xml"), VF_ACTION)
    _, edges = EX.extract(f)
    et = _et(edges)

    assert ("quickaction/MeterPoint__c.Detail", "embeds", "vfpage",
            "AcmeMeterDetail") in et
    # explicit targetObject is used for the object context
    assert ("quickaction/MeterPoint__c.Detail", "on", "object",
            "MeterPoint__c") in et


def test_explicit_target_object_overrides_filename():
    """With both a filename object segment and <targetObject>, the explicit
    <targetObject> wins (single `on` edge)."""
    body = VF_ACTION.replace("<targetObject>MeterPoint__c</targetObject>",
                             "<targetObject>Globex__c</targetObject>")
    f = _w(Path("/tmp/_acme_qa/MeterPoint__c.Detail.quickAction-meta.xml"), body)
    _, edges = EX.extract(f)
    on_targets = [e["to_name"] for e in edges if e["type"] == "on"]
    assert on_targets == ["Globex__c"]


def test_flow_calls_flow():
    """Flow type: <flowDefinition> -> calls -> flow."""
    f = _w(Path("/tmp/_acme_qa/Acme_Onboard.quickAction-meta.xml"), FLOW_ACTION)
    _, edges = EX.extract(f)
    assert ("quickaction/Acme_Onboard", "calls", "flow",
            "Acme_Onboard_Meter") in _et(edges)


def test_already_qualified_layout_field_kept_as_is():
    """A layout <field> that is already Object.Field is not re-qualified."""
    body = CREATE_ACTION.replace("<field>ReadingValue__c</field>",
                                 "<field>Globex__c.Amount__c</field>")
    f = _w(Path("/tmp/_acme_qa/MeterPoint__c.NewReading.quickAction-meta.xml"), body)
    _, edges = EX.extract(f)
    reads = {e["to_name"] for e in edges if e["type"] == "reads"}
    assert "Globex__c.Amount__c" in reads
    # the still-bare sibling field is qualified with the action's object context
    assert "MeterPoint__c.ReadDate__c" in reads


def test_dedup_fields():
    """Repeated layout fields collapse to one reads edge."""
    body = CREATE_ACTION.replace(
        "<field>ReadDate__c</field>", "<field>ReadingValue__c</field>")
    f = _w(Path("/tmp/_acme_qa/MeterPoint__c.NewReading.quickAction-meta.xml"), body)
    _, edges = EX.extract(f)
    reads = [e["to_name"] for e in edges if e["type"] == "reads"]
    assert reads.count("MeterPoint__c.ReadingValue__c") == 1


def test_never_raises_on_broken_xml():
    for text in ("", "not xml at all <<<", "<QuickAction></QuickAction>",
                 "<QuickAction xmlns='http://soap.sforce.com/2006/04/metadata'>"):
        f = _w(Path("/tmp/_acme_qa/Broken.quickAction-meta.xml"), text)
        nodes, edges = EX.extract(f)          # must not raise
        assert isinstance(nodes, list) and isinstance(edges, list)
        assert any(n["type"] == "quickaction" for n in nodes)


def test_build_graph_resolves_and_stubs(tmp_path):
    """Graph build with the default resolvers: object/lwc/flow/vfpage targets
    resolve to external stubs."""
    qa_dir = tmp_path / "force-app" / "main" / "default" / "quickActions"
    _w(qa_dir / "MeterPoint__c.NewReading.quickAction-meta.xml", CREATE_ACTION)
    _w(qa_dir / "AcmeMeterPanel.quickAction-meta.xml", LWC_ACTION)
    _w(qa_dir / "MeterPoint__c.Detail.quickAction-meta.xml", VF_ACTION)
    _w(qa_dir / "Acme_Onboard.quickAction-meta.xml", FLOW_ACTION)

    g = (GraphBuilder()
         .register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    assert g["errors"] == []
    ids = {n["id"]: n for n in g["nodes"]}

    assert "quickaction/MeterPoint__c.NewReading" in ids
    assert "quickaction/AcmeMeterPanel" in ids

    # resolved edges (object/field/lwc/flow are stub kinds)
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert ("quickaction/MeterPoint__c.NewReading", "on",
            "object/MeterPoint__c") in edges
    assert ("quickaction/MeterPoint__c.NewReading", "reads",
            "field/MeterPoint__c.ReadingValue__c") in edges
    assert ("quickaction/AcmeMeterPanel", "embeds", "lwc/acmeMeterPanel") in edges
    assert ("quickaction/Acme_Onboard", "calls", "flow/Acme_Onboard_Meter") in edges

    # the object/lwc/flow targets are external stubs (not in this tree)
    assert ids["object/MeterPoint__c"].get("external") is True
    assert ids["lwc/acmeMeterPanel"].get("external") is True
    assert ids["flow/Acme_Onboard_Meter"].get("external") is True

    # vfpage is a default stub kind -> the embeds edge resolves to an external stub
    assert ids["vfpage/AcmeMeterDetail"].get("external") is True
    assert any(e["type"] == "embeds" and e["dst"] == "vfpage/AcmeMeterDetail"
               for e in g["edges"])
