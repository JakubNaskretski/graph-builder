"""Email Template extractor tests.

Confidentiality is the central concern: subject/body text and any merge tokens
living in the subject/body must never appear in the emitted nodes/edges.
"""
import json
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.emailtemplates import EmailTemplateExtractor

EX = EmailTemplateExtractor()


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


# HTML template bound to MeterPoint__c in the AcmeOnboarding folder. Subject and
# htmlValue carry secret text plus merge tokens that must not be read; only
# relatedEntityType should produce a reference.
HTML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<EmailTemplate xmlns="http://soap.sforce.com/2006/04/metadata">
    <name>Welcome Reading</name>
    <type>html</type>
    <encoding>UTF-8</encoding>
    <available>true</available>
    <relatedEntityType>MeterPoint__c</relatedEntityType>
    <subject>Secret subject for {!MeterPoint__c.SecretSubjectField__c}</subject>
    <htmlValue>&lt;p&gt;Hello {!Contact.SecretBodyField__c}, your password is hunter2&lt;/p&gt;</htmlValue>
    <textValue>Hello {!Contact.SecretBodyField__c}, your password is hunter2</textValue>
</EmailTemplate>
"""

# A text template with no entity binding and no folder nesting.
TEXT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<EmailTemplate xmlns="http://soap.sforce.com/2006/04/metadata">
    <name>Plain Notice</name>
    <type>text</type>
    <subject>Notice</subject>
    <textValue>Body text here.</textValue>
</EmailTemplate>
"""

# Template with a merge token in a structural <field> element (the only place a
# field name is legitimately read) alongside leaky tokens in subject/htmlValue.
STRUCT_MERGE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<EmailTemplate xmlns="http://soap.sforce.com/2006/04/metadata">
    <name>Globex Summary</name>
    <type>custom</type>
    <relatedEntityType>Globex_Account__c</relatedEntityType>
    <field>Globex_Account__c.OwnerName__c</field>
    <subject>{!Globex_Account__c.LeakyBodyField__c}</subject>
    <htmlValue>{!Globex_Account__c.AlsoLeaky__c}</htmlValue>
</EmailTemplate>
"""


def test_node_id_type_folder_and_template_type(tmp_path):
    p = _w(tmp_path / "email" / "AcmeOnboarding" / "Welcome_Reading.email-meta.xml",
           HTML_TEMPLATE)
    nodes, _edges = EX.extract(p)
    by_id = _ids(nodes)
    assert "emailtemplate/Welcome_Reading" in by_id
    n = by_id["emailtemplate/Welcome_Reading"]
    assert n["type"] == "emailtemplate"
    assert n["label"] == "Welcome_Reading"
    assert n["folder"] == "AcmeOnboarding"
    assert n["template_type"] == "html"


def test_references_object_from_related_entity(tmp_path):
    p = _w(tmp_path / "email" / "AcmeOnboarding" / "Welcome_Reading.email-meta.xml",
           HTML_TEMPLATE)
    _nodes, edges = EX.extract(p)
    et = _et(edges)
    assert ("emailtemplate/Welcome_Reading", "references", "object", "MeterPoint__c") in et


def test_subject_and_body_merge_fields_are_never_emitted(tmp_path):
    """Tokens in subject/body must not become `reads` field edges, and the
    secret text must not appear anywhere in the output."""
    p = _w(tmp_path / "email" / "AcmeOnboarding" / "Welcome_Reading.email-meta.xml",
           HTML_TEMPLATE)
    nodes, edges = EX.extract(p)
    # No field edges at all here — the only merge tokens are inside subject/body.
    field_edges = [e for e in edges if e["to_kind"] == "field"]
    assert field_edges == []
    # The secret field names / values must not surface anywhere in the output.
    blob = json.dumps([nodes, edges])
    for secret in ("SecretSubjectField__c", "SecretBodyField__c", "hunter2",
                   "Secret subject", "Hello"):
        assert secret not in blob


def test_text_template_no_entity_no_folder(tmp_path):
    # Not nested under a folder beneath `email/` -> no folder attr.
    p = _w(tmp_path / "Plain_Notice.email-meta.xml", TEXT_TEMPLATE)
    nodes, edges = EX.extract(p)
    n = _ids(nodes)["emailtemplate/Plain_Notice"]
    assert n["template_type"] == "text"
    assert "folder" not in n
    # No entity binding -> no references edge.
    assert [e for e in edges if e["type"] == "references"] == []
    # Body text must never leak.
    assert "Body text here" not in json.dumps([nodes, edges])


def test_merge_field_only_from_safe_structural_field(tmp_path):
    p = _w(tmp_path / "email" / "GlobexReports" / "Globex_Summary.email-meta.xml",
           STRUCT_MERGE_TEMPLATE)
    _nodes, edges = EX.extract(p)
    et = _et(edges)
    # The structural <field> merge token is read.
    assert ("emailtemplate/Globex_Summary", "reads", "field",
            "Globex_Account__c.OwnerName__c") in et
    # The subject/body merge tokens are not read.
    field_targets = {e["to_name"] for e in edges if e["to_kind"] == "field"}
    assert "Globex_Account__c.LeakyBodyField__c" not in field_targets
    assert "Globex_Account__c.AlsoLeaky__c" not in field_targets
    assert "LeakyBodyField__c" not in json.dumps(edges)


def test_malformed_xml_skipped_gracefully(tmp_path):
    p = _w(tmp_path / "email" / "Broken" / "Bad_Template.email-meta.xml",
           "<EmailTemplate><type>html</not-closed>")
    nodes, edges = EX.extract(p)
    # Still emits the node (from the filename), no crash, no edges.
    assert _ids(nodes)["emailtemplate/Bad_Template"]["type"] == "emailtemplate"
    assert edges == []


def test_handles_only_email_meta(tmp_path):
    assert EX.handles(Path("X.email-meta.xml")) is True
    assert EX.handles(Path("X.quickAction-meta.xml")) is False
    assert EX.handles(Path("X.cls")) is False


def test_isolated_build_resolves_object_and_field_via_stub(tmp_path):
    _w(tmp_path / "email" / "GlobexReports" / "Globex_Summary.email-meta.xml",
       STRUCT_MERGE_TEMPLATE)
    result = (
        GraphBuilder()
        .register(EmailTemplateExtractor())
        .register_resolver(*resolvers.default_resolvers())
        .build(tmp_path)
    )
    assert result["errors"] == []
    nids = {n["id"] for n in result["nodes"]}
    assert "emailtemplate/Globex_Summary" in nids
    # object + field targets resolve to external stubs in isolation.
    pairs = {(e["src"], e["type"], e["dst"]) for e in result["edges"]}
    assert ("emailtemplate/Globex_Summary", "references",
            "object/Globex_Account__c") in pairs
    assert ("emailtemplate/Globex_Summary", "reads",
            "field/Globex_Account__c.OwnerName__c") in pairs
    # Confidentiality holds through the full build, too.
    blob = json.dumps(result)
    assert "LeakyBodyField__c" not in blob
    assert "AlsoLeaky__c" not in blob
