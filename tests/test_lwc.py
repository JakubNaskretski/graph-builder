"""LWC extractor tests."""
import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.lwc import EXTRACTORS, LwcExtractor

_JS = """
import { LightningElement, wire, api } from 'lwc';
import getMeterPoints from '@salesforce/apex/AcmeMeterController.getMeterPoints';
import saveReading from '@salesforce/apex/AcmeMeterController.saveReading';
import { getRecord } from 'lightning/uiRecordApi';
import METERPOINT_OBJECT from '@salesforce/schema/MeterPoint__c';
import SERIAL_FIELD from '@salesforce/schema/MeterPoint__c.SerialNumber__c';
import readingLabel from '@salesforce/label/c.Acme_Reading_Title';
import ACME_LOGO from '@salesforce/resourceUrl/AcmeLogo';
import READING_CHANNEL from '@salesforce/messageChannel/Acme_Reading__c';
import childTemplate from 'c/acmeReadingCard';

export default class AcmeMeterPanel extends LightningElement {
    @api recordId;
    @wire(getRecord, { recordId: '$recordId' }) record;
    @wire(getMeterPoints, { area: '$area' }) meterPoints;
}
"""

# template with custom children (kebab-case c- elements) plus standard tags
_HTML = """
<template>
    <lightning-card title="Meters">
        <c-acme-reading-card record-id={recordId}></c-acme-reading-card>
        <c-meter-point-list items={meterPoints}></c-meter-point-list>
        <c-acme-reading-card></c-acme-reading-card>
    </lightning-card>
</template>
"""


def _write_bundle(tmp_path, name, js, html="<template></template>\n"):
    bundle = tmp_path / "force-app" / "main" / "default" / "lwc" / name
    bundle.mkdir(parents=True, exist_ok=True)
    js_path = bundle / f"{name}.js"
    js_path.write_text(js, "utf-8")
    # a non-main sibling file in the bundle (must NOT be handled)
    (bundle / "helper.js").write_text("export const x = 1;\n", "utf-8")
    (bundle / f"{name}.html").write_text(html, "utf-8")
    return js_path


def test_handles_only_the_main_module(tmp_path):
    js_path = _write_bundle(tmp_path, "acmeMeterPanel", _JS)
    ex = LwcExtractor()
    assert ex.source == "salesforce"
    assert ex.handles(js_path) is True
    # sibling helper.js inside the same bundle is NOT the main module
    assert ex.handles(js_path.parent / "helper.js") is False
    # the html template is not handled either
    assert ex.handles(js_path.parent / "acmeMeterPanel.html") is False


def test_extract_emits_lwc_node_and_all_edge_types(tmp_path):
    js_path = _write_bundle(tmp_path, "acmeMeterPanel", _JS, _HTML)
    nodes, edges = LwcExtractor().extract(js_path)

    ids = {n["id"]: n for n in nodes}
    assert "lwc/acmeMeterPanel" in ids
    assert ids["lwc/acmeMeterPanel"]["type"] == "lwc"

    def has(etype, to_kind, to_name):
        return any(
            e["src"] == "lwc/acmeMeterPanel"
            and e["type"] == etype
            and e["to_kind"] == to_kind
            and e["to_name"] == to_name
            for e in edges
        )

    # apex controller (calls) + composed component (uses-component)
    assert has("calls", "apexclass", "AcmeMeterController")
    assert has("uses-component", "lwc", "acmeReadingCard")

    # @salesforce/apex/<Class>.<method> -> aura-enabled apexmethod
    assert has("aura-enabled", "apexmethod", "AcmeMeterController.getMeterPoints")
    assert has("aura-enabled", "apexmethod", "AcmeMeterController.saveReading")

    # @salesforce/schema -> wire to object and to field
    assert has("wire", "object", "MeterPoint__c")
    assert has("wire", "field", "MeterPoint__c.SerialNumber__c")

    # labels / resources / message channels -> uses
    assert has("uses", "label", "c.Acme_Reading_Title")
    assert has("uses", "resource", "AcmeLogo")
    assert has("uses", "messagechannel", "Acme_Reading__c")

    # template composition: <c-...> children -> uses-component (kebab->camel)
    assert has("uses-component", "lwc", "acmeReadingCard")   # also in JS import
    assert has("uses-component", "lwc", "meterPointList")    # template-only

    # wired apex: @wire(getMeterPoints, ...) where getMeterPoints is the apex
    # import binding -> wire to the apexmethod (aura-enabled kept too)
    assert has("wire", "apexmethod", "AcmeMeterController.getMeterPoints")
    assert has("aura-enabled", "apexmethod", "AcmeMeterController.getMeterPoints")


def test_template_components_kebab_to_camel_and_deduped(tmp_path):
    # acmeReadingCard appears in BOTH the JS import and the template; only one edge.
    js_path = _write_bundle(tmp_path, "acmeMeterPanel", _JS, _HTML)
    _, edges = LwcExtractor().extract(js_path)
    reading_card = [
        e for e in edges
        if e["type"] == "uses-component" and e["to_name"] == "acmeReadingCard"
    ]
    assert len(reading_card) == 1
    comps = {e["to_name"] for e in edges if e["type"] == "uses-component"}
    assert {"acmeReadingCard", "meterPointList"} <= comps
    # a component's own tag, were it present, must never self-reference
    assert "acmeMeterPanel" not in comps


def test_template_only_component_without_js_import(tmp_path):
    # JS has no apex/schema/component imports; the only composition is the template.
    js = "import { LightningElement } from 'lwc';\n" \
         "export default class MeterPointList extends LightningElement {}\n"
    html = "<template><c-acme-reading-row></c-acme-reading-row></template>\n"
    js_path = _write_bundle(tmp_path, "meterPointList", js, html)
    _, edges = LwcExtractor().extract(js_path)
    assert any(
        e["type"] == "uses-component"
        and e["to_kind"] == "lwc"
        and e["to_name"] == "acmeReadingRow"
        for e in edges
    )


def test_wire_to_apex_only_for_imported_bindings(tmp_path):
    # @wire(getRecord, ...) is a stdlib adapter (not an apex import) -> no apexmethod
    # wire; @wire(loadStats, ...) IS an apex import -> wire to apexmethod.
    js = (
        "import { LightningElement, wire } from 'lwc';\n"
        "import { getRecord } from 'lightning/uiRecordApi';\n"
        "import loadStats from '@salesforce/apex/MeterPointService.loadStats';\n"
        "export default class MeterStats extends LightningElement {\n"
        "    @wire(getRecord, { recordId: '$recordId' }) rec;\n"
        "    @wire(loadStats, { id: '$recordId' }) stats;\n"
        "}\n"
    )
    js_path = _write_bundle(tmp_path, "meterStats", js)
    _, edges = LwcExtractor().extract(js_path)

    wire_methods = {
        e["to_name"] for e in edges
        if e["type"] == "wire" and e["to_kind"] == "apexmethod"
    }
    assert wire_methods == {"MeterPointService.loadStats"}
    # the apex import still produces its aura-enabled edge independently
    assert any(
        e["type"] == "aura-enabled"
        and e["to_kind"] == "apexmethod"
        and e["to_name"] == "MeterPointService.loadStats"
        for e in edges
    )


def test_broken_or_empty_js_never_raises(tmp_path):
    # garbage / no @salesforce imports -> just the lwc node, no crash
    js_path = _write_bundle(tmp_path, "acmeEmpty", "import nonsense from;;; \x00 not js")
    nodes, edges = LwcExtractor().extract(js_path)
    assert any(n["id"] == "lwc/acmeEmpty" for n in nodes)
    # no apex/schema imports present -> none of the deep edges
    assert all(e["type"] not in {"aura-enabled", "wire"} for e in edges)


def test_build_graph_in_isolation_resolves_to_stubs(tmp_path):
    _write_bundle(tmp_path, "acmeMeterPanel", _JS, _HTML)
    gb = GraphBuilder().register(*EXTRACTORS)
    gb.register_resolver(*resolvers.default_resolvers())
    g = gb.build(tmp_path)

    ids = {n["id"]: n for n in g["nodes"]}
    assert "lwc/acmeMeterPanel" in ids

    # apexmethod / object / field targets become external stubs (not in repo)
    assert ids.get("apexmethod/AcmeMeterController.getMeterPoints", {}).get("external") is True
    assert ids.get("object/MeterPoint__c", {}).get("external") is True
    assert ids.get("field/MeterPoint__c.SerialNumber__c", {}).get("external") is True

    assert any(
        e["type"] == "aura-enabled"
        and e["src"] == "lwc/acmeMeterPanel"
        and e["dst"] == "apexmethod/AcmeMeterController.getMeterPoints"
        for e in g["edges"]
    )
    assert any(
        e["type"] == "wire"
        and e["dst"] == "field/MeterPoint__c.SerialNumber__c"
        for e in g["edges"]
    )

    # wired apex resolves to the apexmethod stub via a wire edge
    assert any(
        e["type"] == "wire"
        and e["src"] == "lwc/acmeMeterPanel"
        and e["dst"] == "apexmethod/AcmeMeterController.getMeterPoints"
        for e in g["edges"]
    )
    # template-only child component resolves to an lwc stub via uses-component
    assert ids.get("lwc/meterPointList", {}).get("external") is True
    assert any(
        e["type"] == "uses-component"
        and e["src"] == "lwc/acmeMeterPanel"
        and e["dst"] == "lwc/meterPointList"
        for e in g["edges"]
    )

    # one bad file/extractor must never kill the build
    assert g["errors"] == []
    # label/resource/messageChannel kinds have no default resolver -> reported, not raised
    assert {u["to_kind"] for u in g["unresolved"]} <= {"label", "resource", "messagechannel"}

def test_template_managed_namespace_children(tmp_path):
    # Managed-package template tags keep their namespace:
    # <acme_pkg-card-frame> -> acme_pkg__cardFrame. Platform tags are skipped.
    html = """
<template>
    <acme_pkg-card-frame></acme_pkg-card-frame>
    <c-acme-reading-card></c-acme-reading-card>
    <lightning-card></lightning-card>
</template>
"""
    js = "import { LightningElement } from 'lwc';\nexport default class NsHost extends LightningElement {}\n"
    js_path = _write_bundle(tmp_path, "nsHost", js, html=html)
    _, edges = LwcExtractor().extract(js_path)
    comps = {e["to_name"] for e in edges if e["type"] == "uses-component"}
    assert "acme_pkg__cardFrame" in comps
    assert "acmeReadingCard" in comps
    assert "lightning__card" not in comps
