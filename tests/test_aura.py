"""Tests for the Aura extractor."""
import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.aura import EXTRACTORS, AuraExtractor

_CMP = """
<aura:component controller="AcmeMeterController" implements="flexipage:availableForRecordHome">
    <aura:attribute name="recordId" type="Id"/>

    <force:recordData aura:id="meterData"
                      recordId="{!v.recordId}"
                      targetObject="MeterPoint__c"
                      targetFields="{!v.simpleRecord}"
                      mode="VIEW"/>

    <aura:dependency resource="markup://c:Account" type="*"/>

    <c:acmeReadingCard record="{!v.simpleRecord}"/>
    <c:meterPointList items="{!v.points}"/>
    <c:acmeReadingCard/>

    <lightning:card title="Meters">
        <ui:button label="Save" press="{!c.handleSave}"/>
    </lightning:card>
</aura:component>
"""

_CONTROLLER_JS = """
({
    handleSave : function(component, event, helper) {
        var action = component.get("apex://AcmeMeterController.saveReading");
        $A.createComponent("c:acmeToast", { message : "saved" }, function(cmp){});
        $A.enqueueAction(action);
    }
})
"""


def _write_bundle(tmp_path, name, cmp, controller_js=None, suffix=".cmp"):
    bundle = tmp_path / "force-app" / "main" / "default" / "aura" / name
    bundle.mkdir(parents=True, exist_ok=True)
    markup_path = bundle / f"{name}{suffix}"
    markup_path.write_text(cmp, "utf-8")
    # a bundle sibling that must NOT be handled (different stem)
    (bundle / f"{name}.cmp-meta.xml").write_text("<x/>\n", "utf-8")
    if controller_js is not None:
        (bundle / f"{name}Controller.js").write_text(controller_js, "utf-8")
    return markup_path


def test_handles_only_the_bundle_markup(tmp_path):
    cmp_path = _write_bundle(tmp_path, "acmeMeterPanel", _CMP)
    ex = AuraExtractor()
    assert ex.source == "salesforce"
    assert ex.handles(cmp_path) is True
    # the *-meta.xml sidecar is not the markup file
    assert ex.handles(cmp_path.parent / "acmeMeterPanel.cmp-meta.xml") is False
    # a .cmp whose stem != bundle dir name is not owned
    other = cmp_path.parent / "notTheBundle.cmp"
    other.write_text("<aura:component/>\n", "utf-8")
    assert ex.handles(other) is False
    # a .cmp not under an `aura/` dir is not owned
    assert ex.handles(tmp_path / "acmeMeterPanel" / "acmeMeterPanel.cmp") is False


def test_handles_app_and_evt_bundles(tmp_path):
    app_path = _write_bundle(tmp_path, "acmeApp", "<aura:application/>\n", suffix=".app")
    evt_path = _write_bundle(tmp_path, "acmeEvent", "<aura:event type='COMPONENT'/>\n", suffix=".evt")
    ex = AuraExtractor()
    assert ex.handles(app_path) is True
    assert ex.handles(evt_path) is True


def test_extract_emits_aura_node_and_all_edge_types(tmp_path):
    cmp_path = _write_bundle(tmp_path, "acmeMeterPanel", _CMP, _CONTROLLER_JS)
    nodes, edges = AuraExtractor().extract(cmp_path)

    ids = {n["id"]: n for n in nodes}
    assert "aura/acmeMeterPanel" in ids
    assert ids["aura/acmeMeterPanel"]["type"] == "aura"
    assert ids["aura/acmeMeterPanel"]["label"] == "acmeMeterPanel"

    def has(etype, to_kind, to_name):
        return any(
            e["src"] == "aura/acmeMeterPanel"
            and e["type"] == etype
            and e["to_kind"] == to_kind
            and e["to_name"] == to_name
            for e in edges
        )

    # uses-component -> aura/<child> from <c:...> markup tags
    assert has("uses-component", "aura", "acmeReadingCard")
    assert has("uses-component", "aura", "meterPointList")
    # uses-component -> aura/<child> from $A.createComponent("c:...") in JS
    assert has("uses-component", "aura", "acmeToast")

    # calls -> apexclass/<Class> from controller="..." attribute
    assert has("calls", "apexclass", "AcmeMeterController")

    # references -> object/<Object> from force:recordData (targetObject) and
    # aura:dependency (resource="markup://c:Account")
    assert has("references", "object", "MeterPoint__c")
    assert has("references", "object", "Account")


def test_uses_component_deduped_and_no_self_reference(tmp_path):
    # acmeReadingCard appears twice in the markup -> only one edge.
    cmp_path = _write_bundle(tmp_path, "acmeMeterPanel", _CMP, _CONTROLLER_JS)
    _, edges = AuraExtractor().extract(cmp_path)
    cards = [
        e for e in edges
        if e["type"] == "uses-component" and e["to_name"] == "acmeReadingCard"
    ]
    assert len(cards) == 1
    comps = {e["to_name"] for e in edges if e["type"] == "uses-component"}
    # a bundle never lists itself as a child even if its own tag appears.
    assert "acmeMeterPanel" not in comps


def test_namespaced_controller_strips_namespace(tmp_path):
    cmp = '<aura:component controller="MyNs.AcmeHandler"></aura:component>\n'
    cmp_path = _write_bundle(tmp_path, "acmeNs", cmp)
    _, edges = AuraExtractor().extract(cmp_path)
    calls = {e["to_name"] for e in edges if e["type"] == "calls" and e["to_kind"] == "apexclass"}
    assert calls == {"AcmeHandler"}


def test_lowercase_binding_not_treated_as_object(tmp_path):
    # a recordData targetFields bound to a lowercase view var must NOT become an object
    cmp = (
        '<aura:component>\n'
        '  <force:recordData targetObject="{!v.simpleRecord}" '
        'targetFields="{!v.fields}"/>\n'
        '  <aura:dependency resource="markup://c:globexThing" type="*"/>\n'
        '</aura:component>\n'
    )
    cmp_path = _write_bundle(tmp_path, "acmeLower", cmp)
    _, edges = AuraExtractor().extract(cmp_path)
    objs = {e["to_name"] for e in edges if e["type"] == "references" and e["to_kind"] == "object"}
    # "{!v.simpleRecord}" is not object-shaped; "globexThing" is lowercase -> skipped
    assert objs == set()


def test_broken_or_empty_markup_never_raises(tmp_path):
    cmp_path = _write_bundle(tmp_path, "acmeEmpty", "<<< not xml \x00 controller= no quotes")
    nodes, edges = AuraExtractor().extract(cmp_path)
    assert any(n["id"] == "aura/acmeEmpty" for n in nodes)
    # nothing object/class/component-shaped present -> no edges (just the node)
    assert edges == []


def test_build_graph_in_isolation_resolves_known_kinds(tmp_path):
    _write_bundle(tmp_path, "acmeMeterPanel", _CMP, _CONTROLLER_JS)
    gb = GraphBuilder().register(*EXTRACTORS)
    gb.register_resolver(*resolvers.default_resolvers())
    g = gb.build(tmp_path)

    ids = {n["id"]: n for n in g["nodes"]}
    assert "aura/acmeMeterPanel" in ids

    # apexclass + object targets are default stub kinds -> external stubs + edges
    assert ids.get("apexclass/AcmeMeterController", {}).get("external") is True
    assert ids.get("object/MeterPoint__c", {}).get("external") is True
    assert ids.get("object/Account", {}).get("external") is True

    assert any(
        e["type"] == "calls"
        and e["src"] == "aura/acmeMeterPanel"
        and e["dst"] == "apexclass/AcmeMeterController"
        for e in g["edges"]
    )
    assert any(
        e["type"] == "references"
        and e["src"] == "aura/acmeMeterPanel"
        and e["dst"] == "object/MeterPoint__c"
        for e in g["edges"]
    )

    # one bad file/extractor must never kill the build
    assert g["errors"] == []

    # `aura` is a default stub kind -> uses-component -> aura/<child> edges resolve
    # to external aura stubs.
    child_dsts = {e["dst"] for e in g["edges"]
                  if e["type"] == "uses-component" and e["src"] == "aura/acmeMeterPanel"}
    assert {"aura/acmeReadingCard", "aura/meterPointList", "aura/acmeToast"} <= child_dsts
    for cid in ("aura/acmeReadingCard", "aura/meterPointList", "aura/acmeToast"):
        assert ids[cid].get("external") is True
    assert g["unresolved"] == []


def test_looks_like_object_namespaced_suffixes():
    from graphbuilder.extractors.aura import _looks_like_object
    # standard + every custom suffix, including managed-package objects whose
    # lowercase namespace prefix would otherwise fail the capitalization fallback
    assert _looks_like_object("Account")
    assert _looks_like_object("MeterPoint__c")
    assert _looks_like_object("vlocity_cmt__Order__c")
    assert _looks_like_object("vlocity_cmt__Event__e")
    assert _looks_like_object("ns__Config__mdt")
    assert _looks_like_object("ns__Ext__x")
    assert _looks_like_object("ns__Big__b")
    # lowercase view bindings / empty are not objects
    assert not _looks_like_object("simpleRecord")
    assert not _looks_like_object("")
