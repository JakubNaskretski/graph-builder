"""Security extractor tests (profiles, permission sets, permission set groups)."""
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.security import SecurityExtractor
from graphbuilder.resolvers import default_resolvers


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


PERMSET = """<?xml version="1.0" encoding="UTF-8"?>
<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Acme Meter Access</label>
    <objectPermissions>
        <object>MeterPoint__c</object>
        <allowRead>true</allowRead>
        <allowEdit>true</allowEdit>
    </objectPermissions>
    <objectPermissions>
        <object>Account</object>
        <allowRead>true</allowRead>
    </objectPermissions>
    <fieldPermissions>
        <field>MeterPoint__c.SerialNumber__c</field>
        <readable>true</readable>
    </fieldPermissions>
    <classAccesses>
        <apexClass>AcmeMeterService</apexClass>
        <enabled>true</enabled>
    </classAccesses>
</PermissionSet>
"""

PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<Profile xmlns="http://soap.sforce.com/2006/04/metadata">
    <objectPermissions>
        <object>MeterPoint__c</object>
        <allowRead>true</allowRead>
    </objectPermissions>
    <classAccesses>
        <apexClass>AcmeBillingController</apexClass>
        <enabled>true</enabled>
    </classAccesses>
</Profile>
"""

PSG = """<?xml version="1.0" encoding="UTF-8"?>
<PermissionSetGroup xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Acme Field Ops</label>
    <permissionSets>AcmeMeterAccess</permissionSets>
    <permissionSets>AcmeBillingAccess</permissionSets>
</PermissionSetGroup>
"""


PERMSET_FULL = """<?xml version="1.0" encoding="UTF-8"?>
<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Acme Full Access</label>
    <objectPermissions>
        <object>MeterPoint__c</object>
        <allowRead>true</allowRead>
        <allowEdit>true</allowEdit>
    </objectPermissions>
    <fieldPermissions>
        <field>MeterPoint__c.SerialNumber__c</field>
        <readable>true</readable>
        <editable>true</editable>
    </fieldPermissions>
    <fieldPermissions>
        <field>MeterPoint__c.Reading__c</field>
        <readable>true</readable>
        <editable>false</editable>
    </fieldPermissions>
    <tabVisibilities>
        <tab>MeterPoint__c</tab>
        <visibility>DefaultOn</visibility>
    </tabVisibilities>
    <applicationVisibilities>
        <application>Acme_Field_Ops</application>
        <visible>true</visible>
        <default>true</default>
    </applicationVisibilities>
    <recordTypeVisibilities>
        <recordType>MeterPoint__c.Residential</recordType>
        <visible>true</visible>
    </recordTypeVisibilities>
    <customPermissions>
        <name>Acme_Override_Lock</name>
        <enabled>true</enabled>
    </customPermissions>
</PermissionSet>
"""


def test_permissionset_nodes_and_grants(tmp_path):
    p = tmp_path / "permissionsets" / "AcmeMeterAccess.permissionset-meta.xml"
    _w(p, PERMSET)
    ex = SecurityExtractor()
    assert ex.handles(p)
    nodes, edges = ex.extract(p)

    (n,) = [x for x in nodes if x["id"] == "permissionset/AcmeMeterAccess"]
    assert n["type"] == "permissionset"
    assert n["label"] == "Acme Meter Access"
    # field grants are kept as a node attr
    assert n["field_grants"] == ["MeterPoint__c.SerialNumber__c"]

    grants = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("grants", "object", "MeterPoint__c") in grants
    assert ("grants", "object", "Account") in grants
    assert ("grants", "apexclass", "AcmeMeterService") in grants
    # field grants are also promoted to edges
    assert ("grants", "field", "MeterPoint__c.SerialNumber__c") in grants
    assert all(e["src"] == "permissionset/AcmeMeterAccess" for e in edges)


def test_field_grants_promoted_to_edges_with_flags(tmp_path):
    p = tmp_path / "permissionsets" / "AcmeFullAccess.permissionset-meta.xml"
    _w(p, PERMSET_FULL)
    nodes, edges = SecurityExtractor().extract(p)

    (n,) = nodes
    # the flat attr is kept...
    assert n["field_grants"] == ["MeterPoint__c.Reading__c", "MeterPoint__c.SerialNumber__c"]
    # ...and readable/editable are mirrored on the node so they survive a build
    assert n["field_access"]["MeterPoint__c.SerialNumber__c"] == {
        "readable": True, "editable": True,
    }
    assert n["field_access"]["MeterPoint__c.Reading__c"] == {
        "readable": True, "editable": False,
    }

    field_edges = {
        e["to_name"]: e for e in edges
        if e["type"] == "grants" and e["to_kind"] == "field"
    }
    assert set(field_edges) == {
        "MeterPoint__c.SerialNumber__c", "MeterPoint__c.Reading__c",
    }
    # readable/editable flags ride on the edge too
    assert field_edges["MeterPoint__c.SerialNumber__c"]["readable"] is True
    assert field_edges["MeterPoint__c.SerialNumber__c"]["editable"] is True
    assert field_edges["MeterPoint__c.Reading__c"]["editable"] is False


def test_visibility_grants_promoted_to_edges(tmp_path):
    p = tmp_path / "permissionsets" / "AcmeFullAccess.permissionset-meta.xml"
    _w(p, PERMSET_FULL)
    nodes, edges = SecurityExtractor().extract(p)
    (n,) = nodes

    grants = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    # tab visibility -> tab
    assert ("grants", "tab", "MeterPoint__c") in grants
    # application visibility -> app, with visible/default flags + node mirror
    assert ("grants", "app", "Acme_Field_Ops") in grants
    (app_edge,) = [e for e in edges if e["to_kind"] == "app"]
    assert app_edge["visible"] is True and app_edge["default"] is True
    assert n["app_visibilities"]["Acme_Field_Ops"] == {"visible": True, "default": True}
    # custom permission -> custompermission
    assert ("grants", "custompermission", "Acme_Override_Lock") in grants


def test_record_type_visibility_carries_record_type_attr(tmp_path):
    p = tmp_path / "permissionsets" / "AcmeFullAccess.permissionset-meta.xml"
    _w(p, PERMSET_FULL)
    nodes, edges = SecurityExtractor().extract(p)
    (n,) = nodes

    rt_edges = [
        e for e in edges
        if e["type"] == "grants" and e["to_kind"] == "object"
        and e.get("record_type")
    ]
    (rt,) = rt_edges
    assert rt["to_name"] == "MeterPoint__c"
    assert rt["record_type"] == "Residential"
    # node-side mirror survives a build
    assert n["record_type_visibilities"] == {"MeterPoint__c": ["Residential"]}
    # the plain objectPermissions grant (no record_type) is still present too
    plain = {
        (e["to_kind"], e["to_name"]) for e in edges
        if e["type"] == "grants" and e["to_kind"] == "object" and not e.get("record_type")
    }
    assert ("object", "MeterPoint__c") in plain


def test_profile_nodes_and_grants(tmp_path):
    p = tmp_path / "profiles" / "AcmeFieldTech.profile-meta.xml"
    _w(p, PROFILE)
    ex = SecurityExtractor()
    assert ex.handles(p)
    nodes, edges = ex.extract(p)

    (n,) = nodes
    assert n["id"] == "profile/AcmeFieldTech"
    assert n["type"] == "profile"

    grants = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("grants", "object", "MeterPoint__c") in grants
    assert ("grants", "apexclass", "AcmeBillingController") in grants


def test_permsetgroup_contains_members(tmp_path):
    p = tmp_path / "permissionsetgroups" / "AcmeFieldOps.permissionsetgroup-meta.xml"
    _w(p, PSG)
    ex = SecurityExtractor()
    assert ex.handles(p)
    nodes, edges = ex.extract(p)

    (n,) = nodes
    assert n["id"] == "permsetgroup/AcmeFieldOps"
    assert n["type"] == "permsetgroup"
    assert n["label"] == "Acme Field Ops"

    members = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("contains", "permissionset", "AcmeMeterAccess") in members
    assert ("contains", "permissionset", "AcmeBillingAccess") in members
    assert all(e["src"] == "permsetgroup/AcmeFieldOps" for e in edges)


def test_handles_rejects_other_files(tmp_path):
    ex = SecurityExtractor()
    assert not ex.handles(tmp_path / "foo.object-meta.xml")
    assert not ex.handles(tmp_path / "AcmeMeterService.cls")
    assert not ex.handles(tmp_path / "Something.trigger")


def test_broken_xml_does_not_raise(tmp_path):
    p = tmp_path / "permissionsets" / "AcmeBroken.permissionset-meta.xml"
    _w(p, "<PermissionSet><object>oops unclosed")
    ex = SecurityExtractor()
    # never raise on odd/broken input — emit the node, skip the unparseable refs
    nodes, edges = ex.extract(p)
    assert nodes[0]["id"] == "permissionset/AcmeBroken"
    assert edges == []


def test_graph_build_in_isolation(tmp_path):
    """Graph build with the default resolvers: object/class targets become external
    stubs and edges resolve."""
    _w(tmp_path / "permissionsets" / "AcmeMeterAccess.permissionset-meta.xml", PERMSET)
    _w(tmp_path / "permissionsetgroups" / "AcmeFieldOps.permissionsetgroup-meta.xml", PSG)

    g = (
        GraphBuilder()
        .register(SecurityExtractor())
        .register_resolver(*default_resolvers())
        .build(tmp_path)
    )
    ids = {n["id"]: n for n in g["nodes"]}
    assert "permissionset/AcmeMeterAccess" in ids
    assert "permsetgroup/AcmeFieldOps" in ids
    # grants target resolves to an external stub object/class (not in the repo)
    assert ids["object/MeterPoint__c"].get("external") is True
    assert ids["apexclass/AcmeMeterService"].get("external") is True
    # group member resolves to a permissionset stub
    assert ids["permissionset/AcmeBillingAccess"].get("external") is True

    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert ("permissionset/AcmeMeterAccess", "grants", "object/MeterPoint__c") in edges
    assert ("permissionset/AcmeMeterAccess", "grants", "apexclass/AcmeMeterService") in edges
    # promoted field grant resolves to a field stub
    assert (
        "permissionset/AcmeMeterAccess", "grants", "field/MeterPoint__c.SerialNumber__c",
    ) in edges
    assert ("permsetgroup/AcmeFieldOps", "contains", "permissionset/AcmeMeterAccess") in edges
    assert g["errors"] == [] and g["unresolved"] == []


def test_build_promoted_visibility_edges_resolve(tmp_path):
    """field/tab/app/object/custompermission targets all have default stub
    resolvers, so their grants edges resolve to external stubs."""
    _w(tmp_path / "permissionsets" / "AcmeFullAccess.permissionset-meta.xml", PERMSET_FULL)

    g = (
        GraphBuilder()
        .register(SecurityExtractor())
        .register_resolver(*default_resolvers())
        .build(tmp_path)
    )
    ids = {n["id"]: n for n in g["nodes"]}
    assert ids["field/MeterPoint__c.SerialNumber__c"].get("external") is True
    assert ids["tab/MeterPoint__c"].get("external") is True
    assert ids["app/Acme_Field_Ops"].get("external") is True

    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    src = "permissionset/AcmeFullAccess"
    assert (src, "grants", "field/MeterPoint__c.SerialNumber__c") in edges
    assert (src, "grants", "field/MeterPoint__c.Reading__c") in edges
    assert (src, "grants", "tab/MeterPoint__c") in edges
    assert (src, "grants", "app/Acme_Field_Ops") in edges
    assert (src, "grants", "object/MeterPoint__c") in edges  # record type + plain

    # custompermission is a default stub kind -> the grants edge resolves to an
    # external custompermission stub
    assert ids["custompermission/Acme_Override_Lock"].get("external") is True
    assert (src, "grants", "custompermission/Acme_Override_Lock") in edges
    assert g["errors"] == []
