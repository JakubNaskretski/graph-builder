"""Sharing-rules extractor tests."""
from __future__ import annotations

import textwrap

from graphbuilder.core import GraphBuilder
from graphbuilder.extractors import sharingrules as sr
from graphbuilder.resolvers import default_resolvers


SHARING_XML = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <SharingRules xmlns="http://soap.sforce.com/2006/04/metadata">
        <sharingCriteriaRules>
            <fullName>Share_Active_Meters</fullName>
            <accessLevel>Read</accessLevel>
            <label>Share Active Meters</label>
            <sharedTo>
                <group>AcmeOpsGroup</group>
            </sharedTo>
            <criteriaItems>
                <field>MeterPoint__c.Status__c</field>
                <operation>equals</operation>
                <value>SECRET-ACTIVE-VALUE</value>
            </criteriaItems>
            <criteriaItems>
                <field>MeterPoint__c.Region__c</field>
                <operation>equals</operation>
                <value>SECRET-REGION-VALUE</value>
            </criteriaItems>
        </sharingCriteriaRules>
        <sharingOwnerRules>
            <fullName>Owner_To_Globex</fullName>
            <accessLevel>Edit</accessLevel>
            <sharedTo>
                <role>GlobexManagerRole</role>
            </sharedTo>
            <sharedFrom>
                <role>AcmeAgentRole</role>
            </sharedFrom>
        </sharingOwnerRules>
        <sharingGuestRules>
            <fullName>Guest_Public</fullName>
            <accessLevel>Read</accessLevel>
            <sharedTo>
                <group>AcmeGuestGroup</group>
            </sharedTo>
            <criteriaItems>
                <field>MeterPoint__c.IsPublic__c</field>
                <operation>equals</operation>
                <value>SECRET-GUEST-VALUE</value>
            </criteriaItems>
        </sharingGuestRules>
    </SharingRules>
    """
)


def _write_repo(tmp_path):
    d = tmp_path / "sharingRules"
    d.mkdir()
    (d / "MeterPoint__c.sharingRules-meta.xml").write_text(SHARING_XML, "utf-8")
    return tmp_path


def _build(tmp_path):
    return (
        GraphBuilder()
        .register(sr.SharingRulesExtractor())
        .register_resolver(*default_resolvers())
        .build(tmp_path)
    )


def test_handles():
    ex = sr.SharingRulesExtractor()
    from pathlib import Path

    assert ex.handles(Path("sharingRules/MeterPoint__c.sharingRules-meta.xml"))
    assert not ex.handles(Path("MeterPoint__c.object-meta.xml"))
    assert not ex.handles(Path("Foo.permissionset-meta.xml"))


def test_rule_nodes_and_types():
    ex = sr.SharingRulesExtractor()
    from pathlib import Path
    import tempfile
    import os

    tmp = Path(tempfile.mkdtemp())
    p = tmp / "MeterPoint__c.sharingRules-meta.xml"
    p.write_text(SHARING_XML, "utf-8")
    nodes, edges = ex.extract(p)

    byid = {n["id"]: n for n in nodes}
    assert "sharingrule/MeterPoint__c.Share_Active_Meters" in byid
    assert "sharingrule/MeterPoint__c.Owner_To_Globex" in byid
    assert "sharingrule/MeterPoint__c.Guest_Public" in byid

    assert byid["sharingrule/MeterPoint__c.Share_Active_Meters"]["rule_type"] == "criteria"
    assert byid["sharingrule/MeterPoint__c.Owner_To_Globex"]["rule_type"] == "owner"
    assert byid["sharingrule/MeterPoint__c.Guest_Public"]["rule_type"] == "guest"

    # every rule node is typed "sharingrule"
    assert all(n["type"] == "sharingrule" for n in nodes)


def test_shared_to_attr_and_principal_edges():
    ex = sr.SharingRulesExtractor()
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    p = tmp / "MeterPoint__c.sharingRules-meta.xml"
    p.write_text(SHARING_XML, "utf-8")
    nodes, edges = ex.extract(p)
    byid = {n["id"]: n for n in nodes}

    # the developerName is kept as a flat attr
    assert byid["sharingrule/MeterPoint__c.Share_Active_Meters"]["shared_to"] == "AcmeOpsGroup"
    assert byid["sharingrule/MeterPoint__c.Owner_To_Globex"]["shared_to"] == "GlobexManagerRole"
    assert byid["sharingrule/MeterPoint__c.Guest_Public"]["shared_to"] == "AcmeGuestGroup"

    # group/role principals also become `references` edges
    triples = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("sharingrule/MeterPoint__c.Share_Active_Meters",
            "references", "publicgroup", "AcmeOpsGroup") in triples
    assert ("sharingrule/MeterPoint__c.Owner_To_Globex",
            "references", "role", "GlobexManagerRole") in triples
    assert ("sharingrule/MeterPoint__c.Guest_Public",
            "references", "publicgroup", "AcmeGuestGroup") in triples


def test_on_object_edges():
    ex = sr.SharingRulesExtractor()
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    p = tmp / "MeterPoint__c.sharingRules-meta.xml"
    p.write_text(SHARING_XML, "utf-8")
    nodes, edges = ex.extract(p)

    on_edges = [e for e in edges if e["type"] == "on"]
    # one per rule, all to the governed object from the filename
    assert len(on_edges) == 3
    assert all(e["to_kind"] == "object" and e["to_name"] == "MeterPoint__c" for e in on_edges)


def test_criteria_field_reads():
    ex = sr.SharingRulesExtractor()
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    p = tmp / "MeterPoint__c.sharingRules-meta.xml"
    p.write_text(SHARING_XML, "utf-8")
    nodes, edges = ex.extract(p)

    reads = {(e["src"], e["to_name"]) for e in edges if e["type"] == "reads"}
    assert ("sharingrule/MeterPoint__c.Share_Active_Meters", "MeterPoint__c.Status__c") in reads
    assert ("sharingrule/MeterPoint__c.Share_Active_Meters", "MeterPoint__c.Region__c") in reads
    # guest rule's criteria field is also read
    assert ("sharingrule/MeterPoint__c.Guest_Public", "MeterPoint__c.IsPublic__c") in reads
    # owner rule has no criteria -> no reads edge from it
    assert not any(e["type"] == "reads" and e["src"].endswith("Owner_To_Globex") for e in edges)
    assert all(e["to_kind"] == "field" for e in edges if e["type"] == "reads")


def test_no_values_leak():
    """Confidentiality: criterion <value> text must never appear anywhere."""
    ex = sr.SharingRulesExtractor()
    from pathlib import Path
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    p = tmp / "MeterPoint__c.sharingRules-meta.xml"
    p.write_text(SHARING_XML, "utf-8")
    nodes, edges = ex.extract(p)

    blob = repr(nodes) + repr(edges)
    assert "SECRET" not in blob
    assert "operation" not in blob
    assert "equals" not in blob
    # label text must not leak either
    assert "Share Active Meters" not in blob


def test_build_resolves_object_and_fields(tmp_path):
    g = _build(_write_repo(tmp_path))
    ids = {n["id"]: n for n in g["nodes"]}

    # rule nodes present
    assert "sharingrule/MeterPoint__c.Share_Active_Meters" in ids
    # object resolved as an external stub (not in the repo)
    assert "object/MeterPoint__c" in ids
    assert ids["object/MeterPoint__c"].get("external") is True
    # on edge resolved
    assert any(
        e["src"] == "sharingrule/MeterPoint__c.Share_Active_Meters"
        and e["dst"] == "object/MeterPoint__c"
        and e["type"] == "on"
        for e in g["edges"]
    )
    # criteria field read resolved to a field stub
    assert any(
        e["type"] == "reads"
        and e["src"] == "sharingrule/MeterPoint__c.Share_Active_Meters"
        and e["dst"] == "field/MeterPoint__c.Status__c"
        for e in g["edges"]
    )
    # no errors during build
    assert g["errors"] == []


def test_bad_xml_is_skipped(tmp_path):
    d = tmp_path / "sharingRules"
    d.mkdir()
    (d / "Globex__c.sharingRules-meta.xml").write_text("<SharingRules><broken", "utf-8")
    g = _build(tmp_path)
    # no rule nodes, no crash
    assert not any(n["id"].startswith("sharingrule/") for n in g["nodes"])
    assert g["errors"] == []


def test_rule_without_fullname_skipped():
    ex = sr.SharingRulesExtractor()
    from pathlib import Path
    import tempfile

    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <SharingRules xmlns="http://soap.sforce.com/2006/04/metadata">
            <sharingCriteriaRules>
                <accessLevel>Read</accessLevel>
            </sharingCriteriaRules>
        </SharingRules>
        """
    )
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "Globex__c.sharingRules-meta.xml"
    p.write_text(xml, "utf-8")
    nodes, edges = ex.extract(p)
    assert nodes == []
    assert edges == []


def test_all_internal_users_no_shared_to_attr():
    """A constant share target (<allInternalUsers/>) has no developerName, so
    `shared_to` is simply absent — never an empty/garbage value."""
    ex = sr.SharingRulesExtractor()
    from pathlib import Path
    import tempfile

    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <SharingRules xmlns="http://soap.sforce.com/2006/04/metadata">
            <sharingCriteriaRules>
                <fullName>All_Internal</fullName>
                <accessLevel>Read</accessLevel>
                <sharedTo>
                    <allInternalUsers></allInternalUsers>
                </sharedTo>
                <criteriaItems>
                    <field>Globex__c.Tier__c</field>
                    <operation>equals</operation>
                    <value>SECRET</value>
                </criteriaItems>
            </sharingCriteriaRules>
        </SharingRules>
        """
    )
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "Globex__c.sharingRules-meta.xml"
    p.write_text(xml, "utf-8")
    nodes, edges = ex.extract(p)
    byid = {n["id"]: n for n in nodes}
    rule = byid["sharingrule/Globex__c.All_Internal"]
    assert "shared_to" not in rule
    assert rule["rule_type"] == "criteria"
