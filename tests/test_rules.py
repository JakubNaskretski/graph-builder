"""Record-action rules extractor tests.

Covers the four rule types: assignmentRules, escalationRules, duplicateRule,
matchingRule. The confidentiality property — criterion values never leak, only
field names — is asserted across all of them.
"""
from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

from graphbuilder.core import GraphBuilder
from graphbuilder.extractors import rules as rl
from graphbuilder.resolvers import default_resolvers


# --------------------------------------------------------------------------- #
# fixtures (every <value> carries a SECRET marker so a leak is unmistakable)
# --------------------------------------------------------------------------- #
ASSIGNMENT_XML = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <AssignmentRules xmlns="http://soap.sforce.com/2006/04/metadata">
        <assignmentRule>
            <fullName>Route_Acme_Leads</fullName>
            <active>true</active>
            <ruleEntries>
                <criteriaItems>
                    <field>Lead.Industry</field>
                    <operation>equals</operation>
                    <value>SECRET-INDUSTRY</value>
                </criteriaItems>
                <criteriaItems>
                    <field>Lead.Region__c</field>
                    <operation>equals</operation>
                    <value>SECRET-REGION</value>
                </criteriaItems>
                <assignedTo>AcmeQueue</assignedTo>
            </ruleEntries>
        </assignmentRule>
        <assignmentRule>
            <fullName>Route_Globex_Leads</fullName>
            <active>false</active>
            <ruleEntries>
                <criteriaItems>
                    <field>Lead.Rating</field>
                    <operation>equals</operation>
                    <value>SECRET-RATING</value>
                </criteriaItems>
            </ruleEntries>
        </assignmentRule>
    </AssignmentRules>
    """
)

ESCALATION_XML = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <EscalationRules xmlns="http://soap.sforce.com/2006/04/metadata">
        <escalationRule>
            <fullName>Escalate_Acme_Cases</fullName>
            <active>true</active>
            <ruleEntries>
                <criteriaItems>
                    <field>Case.Priority</field>
                    <operation>equals</operation>
                    <value>SECRET-PRIORITY</value>
                </criteriaItems>
                <businessHours>SECRET-HOURS</businessHours>
            </ruleEntries>
        </escalationRule>
    </EscalationRules>
    """
)

DUPLICATE_XML = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <DuplicateRule xmlns="http://soap.sforce.com/2006/04/metadata">
        <fullName>Account.Acme_Account_Dedupe</fullName>
        <isActive>true</isActive>
        <masterLabel>SECRET-LABEL-TEXT</masterLabel>
        <duplicateRuleFilterItems>
            <field>Account.BillingCountry</field>
            <operation>equals</operation>
            <value>SECRET-COUNTRY</value>
        </duplicateRuleFilterItems>
        <duplicateRuleMatchRules>
            <matchRuleSObjectType>Account</matchRuleSObjectType>
            <matchingRule>Acme_Account_Match</matchingRule>
        </duplicateRuleMatchRules>
    </DuplicateRule>
    """
)

MATCHING_XML = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <MatchingRules xmlns="http://soap.sforce.com/2006/04/metadata">
        <matchingRules>
            <fullName>Acme_Account_Match</fullName>
            <label>SECRET-MATCH-LABEL</label>
            <ruleStatus>Active</ruleStatus>
            <matchingRuleItems>
                <fieldName>Name</fieldName>
                <matchingMethod>Exact</matchingMethod>
                <blankValueBehavior>NullNotAllowed</blankValueBehavior>
            </matchingRuleItems>
            <matchingRuleItems>
                <fieldName>BillingStreet</fieldName>
                <matchingMethod>FuzzyStreetNumber</matchingMethod>
            </matchingRuleItems>
        </matchingRules>
    </MatchingRules>
    """
)


def _tmp(name: str, xml: str) -> Path:
    p = Path(tempfile.mkdtemp()) / name
    p.write_text(xml, "utf-8")
    return p


def _build(tmp_path):
    return (
        GraphBuilder()
        .register(rl.RuleExtractor())
        .register_resolver(*default_resolvers())
        .build(tmp_path)
    )


# --------------------------------------------------------------------------- #
# handles
# --------------------------------------------------------------------------- #
def test_handles_all_four_and_rejects_others():
    ex = rl.RuleExtractor()
    assert ex.handles(Path("Lead.assignmentRules-meta.xml"))
    assert ex.handles(Path("Case.escalationRules-meta.xml"))
    assert ex.handles(Path("Account.Acme_Dedupe.duplicateRule-meta.xml"))
    assert ex.handles(Path("Account.matchingRule-meta.xml"))
    # workflow rules (out of scope) and unrelated metadata are rejected
    assert not ex.handles(Path("Lead.workflow-meta.xml"))
    assert not ex.handles(Path("MeterPoint__c.sharingRules-meta.xml"))
    assert not ex.handles(Path("MeterPoint__c.object-meta.xml"))


# --------------------------------------------------------------------------- #
# assignment rules
# --------------------------------------------------------------------------- #
def test_assignment_nodes_and_edges():
    ex = rl.RuleExtractor()
    p = _tmp("Lead.assignmentRules-meta.xml", ASSIGNMENT_XML)
    nodes, edges = ex.extract(p)
    byid = {n["id"]: n for n in nodes}

    assert "assignmentrule/Lead.Route_Acme_Leads" in byid
    assert "assignmentrule/Lead.Route_Globex_Leads" in byid
    assert all(n["type"] == "assignmentrule" for n in nodes)
    assert byid["assignmentrule/Lead.Route_Acme_Leads"]["label"] == "Route_Acme_Leads"

    on = {(e["src"], e["to_name"]) for e in edges if e["type"] == "on"}
    assert ("assignmentrule/Lead.Route_Acme_Leads", "Lead") in on
    assert ("assignmentrule/Lead.Route_Globex_Leads", "Lead") in on
    assert all(e["to_kind"] == "object" for e in edges if e["type"] == "on")

    reads = {(e["src"], e["to_name"]) for e in edges if e["type"] == "reads"}
    assert ("assignmentrule/Lead.Route_Acme_Leads", "Lead.Industry") in reads
    assert ("assignmentrule/Lead.Route_Acme_Leads", "Lead.Region__c") in reads
    assert ("assignmentrule/Lead.Route_Globex_Leads", "Lead.Rating") in reads
    assert all(e["to_kind"] == "field" for e in edges if e["type"] == "reads")


# --------------------------------------------------------------------------- #
# escalation rules
# --------------------------------------------------------------------------- #
def test_escalation_nodes_and_edges():
    ex = rl.RuleExtractor()
    p = _tmp("Case.escalationRules-meta.xml", ESCALATION_XML)
    nodes, edges = ex.extract(p)
    byid = {n["id"]: n for n in nodes}

    assert "escalationrule/Case.Escalate_Acme_Cases" in byid
    assert byid["escalationrule/Case.Escalate_Acme_Cases"]["type"] == "escalationrule"

    on = [e for e in edges if e["type"] == "on"]
    assert on and all(e["to_kind"] == "object" and e["to_name"] == "Case" for e in on)

    reads = {(e["src"], e["to_name"]) for e in edges if e["type"] == "reads"}
    assert ("escalationrule/Case.Escalate_Acme_Cases", "Case.Priority") in reads


# --------------------------------------------------------------------------- #
# duplicate rule (single rule per file; object from the fullName prefix)
# --------------------------------------------------------------------------- #
def test_duplicate_node_and_edges():
    ex = rl.RuleExtractor()
    p = _tmp("Account.Acme_Account_Dedupe.duplicateRule-meta.xml", DUPLICATE_XML)
    nodes, edges = ex.extract(p)
    byid = {n["id"]: n for n in nodes}

    # node id is "<ruletype>/<Object>.<ruleName>", derived from the fullName
    assert "duplicaterule/Account.Acme_Account_Dedupe" in byid
    assert byid["duplicaterule/Account.Acme_Account_Dedupe"]["type"] == "duplicaterule"

    on = [e for e in edges if e["type"] == "on"]
    assert on and all(e["to_kind"] == "object" and e["to_name"] == "Account" for e in on)

    reads = {(e["src"], e["to_name"]) for e in edges if e["type"] == "reads"}
    assert ("duplicaterule/Account.Acme_Account_Dedupe", "Account.BillingCountry") in reads


def test_duplicate_object_from_sobjecttype_when_no_dot_in_fullname():
    """A duplicate rule whose fullName has no dotted object prefix falls back to
    <sobjectType> / <matchRuleSObjectType> for the governed object."""
    ex = rl.RuleExtractor()
    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <DuplicateRule xmlns="http://soap.sforce.com/2006/04/metadata">
            <fullName>Globex_Dedupe</fullName>
            <sobjectType>Contact</sobjectType>
            <duplicateRuleFilterItems>
                <field>Email</field>
                <operation>equals</operation>
                <value>SECRET</value>
            </duplicateRuleFilterItems>
        </DuplicateRule>
        """
    )
    p = _tmp("Globex_Dedupe.duplicateRule-meta.xml", xml)
    nodes, edges = ex.extract(p)
    on = [e for e in edges if e["type"] == "on"]
    assert on and on[0]["to_name"] == "Contact"
    # bare criterion field is qualified with the governed object
    reads = {e["to_name"] for e in edges if e["type"] == "reads"}
    assert "Contact.Email" in reads


# --------------------------------------------------------------------------- #
# matching rule (multi-rule file; <fieldName> refs)
# --------------------------------------------------------------------------- #
def test_matching_node_and_edges():
    ex = rl.RuleExtractor()
    p = _tmp("Account.matchingRule-meta.xml", MATCHING_XML)
    nodes, edges = ex.extract(p)
    byid = {n["id"]: n for n in nodes}

    assert "matchingrule/Account.Acme_Account_Match" in byid
    assert byid["matchingrule/Account.Acme_Account_Match"]["type"] == "matchingrule"

    on = [e for e in edges if e["type"] == "on"]
    assert on and all(e["to_kind"] == "object" and e["to_name"] == "Account" for e in on)

    # bare <fieldName> values are qualified with the governed object
    reads = {(e["src"], e["to_name"]) for e in edges if e["type"] == "reads"}
    assert ("matchingrule/Account.Acme_Account_Match", "Account.Name") in reads
    assert ("matchingrule/Account.Acme_Account_Match", "Account.BillingStreet") in reads
    assert all(e["to_kind"] == "field" for e in edges if e["type"] == "reads")


# --------------------------------------------------------------------------- #
# confidentiality — no values, labels, operations, or subjects leak
# --------------------------------------------------------------------------- #
def test_no_values_leak_across_all_types():
    ex = rl.RuleExtractor()
    blob = ""
    for name, xml in (
        ("Lead.assignmentRules-meta.xml", ASSIGNMENT_XML),
        ("Case.escalationRules-meta.xml", ESCALATION_XML),
        ("Account.Acme_Account_Dedupe.duplicateRule-meta.xml", DUPLICATE_XML),
        ("Account.matchingRule-meta.xml", MATCHING_XML),
    ):
        nodes, edges = ex.extract(_tmp(name, xml))
        blob += repr(nodes) + repr(edges)

    # no criterion values, operations, business hours, match methods, or labels
    assert "SECRET" not in blob
    assert "equals" not in blob
    assert "operation" not in blob
    assert "Exact" not in blob
    assert "FuzzyStreetNumber" not in blob
    assert "matchingMethod" not in blob
    assert "assignedTo" not in blob
    assert "AcmeQueue" not in blob


# --------------------------------------------------------------------------- #
# build / resolution
# --------------------------------------------------------------------------- #
def test_build_resolves_objects_and_fields(tmp_path):
    (tmp_path / "Lead.assignmentRules-meta.xml").write_text(ASSIGNMENT_XML, "utf-8")
    (tmp_path / "Account.matchingRule-meta.xml").write_text(MATCHING_XML, "utf-8")
    g = _build(tmp_path)
    ids = {n["id"]: n for n in g["nodes"]}

    assert "assignmentrule/Lead.Route_Acme_Leads" in ids
    assert "matchingrule/Account.Acme_Account_Match" in ids

    # object resolved as an external stub (not in the repo)
    assert ids.get("object/Lead", {}).get("external") is True
    assert ids.get("object/Account", {}).get("external") is True

    # on edge resolved
    assert any(
        e["src"] == "assignmentrule/Lead.Route_Acme_Leads"
        and e["dst"] == "object/Lead"
        and e["type"] == "on"
        for e in g["edges"]
    )
    # criterion field read resolved to a field stub
    assert any(
        e["type"] == "reads"
        and e["src"] == "assignmentrule/Lead.Route_Acme_Leads"
        and e["dst"] == "field/Lead.Industry"
        for e in g["edges"]
    )
    assert g["errors"] == []


# --------------------------------------------------------------------------- #
# robustness
# --------------------------------------------------------------------------- #
def test_bad_xml_is_skipped(tmp_path):
    (tmp_path / "Lead.assignmentRules-meta.xml").write_text("<AssignmentRules><broke", "utf-8")
    g = _build(tmp_path)
    assert not any(n["id"].startswith("assignmentrule/") for n in g["nodes"])
    assert g["errors"] == []


def test_rule_without_fullname_skipped():
    ex = rl.RuleExtractor()
    xml = textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <AssignmentRules xmlns="http://soap.sforce.com/2006/04/metadata">
            <assignmentRule>
                <active>true</active>
            </assignmentRule>
        </AssignmentRules>
        """
    )
    nodes, edges = ex.extract(_tmp("Lead.assignmentRules-meta.xml", xml))
    assert nodes == []
    assert edges == []


def test_extract_never_raises_on_missing_file():
    ex = rl.RuleExtractor()
    # nonexistent path: the parse error is swallowed, yielding an empty result
    nodes, edges = ex.extract(Path("/nonexistent/Lead.assignmentRules-meta.xml"))
    assert nodes == []
    assert edges == []
