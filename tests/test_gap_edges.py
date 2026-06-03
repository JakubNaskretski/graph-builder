"""Dependency edges spanning several extractors.

Each test drives one extractor's `extract()` on inline metadata, asserts the
dependency edge, and checks that no value data leaks.
"""
from __future__ import annotations

from graphbuilder.extractors.security import SecurityExtractor
from graphbuilder.extractors.sharingrules import SharingRulesExtractor
from graphbuilder.extractors.groups import GroupingExtractor
from graphbuilder.extractors.approvalprocesses import ApprovalProcessExtractor
from graphbuilder.extractors.apex import ApexExtractor
from graphbuilder.extractors import rules as rl
from graphbuilder.extractors import flows

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _triples(edges):
    return {(e["type"], e["to_kind"], e["to_name"]) for e in edges}


# security: pageAccesses -> vfpage, flowAccesses -> flow, CMT/CS -> object
def test_security_page_flow_and_data_grants(tmp_path):
    xml = (
        f'<?xml version="1.0"?><PermissionSet {NS}><label>Ops</label>'
        "<pageAccesses><apexPage>Acme_Console</apexPage><enabled>true</enabled></pageAccesses>"
        "<flowAccesses><flow>Acme_Onboarding</flow><enabled>true</enabled></flowAccesses>"
        "<customMetadataTypeAccesses><name>Acme_Cfg__mdt</name><enabled>true</enabled></customMetadataTypeAccesses>"
        "<customSettingAccesses><name>Acme_Settings__c</name><enabled>true</enabled></customSettingAccesses>"
        "</PermissionSet>"
    )
    nodes, edges = SecurityExtractor().extract(_w(tmp_path / "Acme_Ops.permissionset-meta.xml", xml))
    t = _triples(edges)
    assert ("grants", "vfpage", "Acme_Console") in t
    assert ("grants", "flow", "Acme_Onboarding") in t
    assert ("grants", "object", "Acme_Cfg__mdt") in t
    assert ("grants", "object", "Acme_Settings__c") in t


# sharing rules: <sharedTo>/<sharedFrom> principals -> role / publicgroup
def test_sharingrule_principal_edges(tmp_path):
    xml = (
        f'<?xml version="1.0"?><SharingRules {NS}>'
        "<sharingOwnerRules><fullName>Acme_Owner</fullName>"
        "<sharedTo><group>Acme_Ops</group></sharedTo>"
        "<sharedFrom><role>Acme_Reps</role></sharedFrom></sharingOwnerRules>"
        "</SharingRules>"
    )
    _, edges = SharingRulesExtractor().extract(_w(tmp_path / "MeterPoint__c.sharingRules-meta.xml", xml))
    t = _triples(edges)
    assert ("references", "publicgroup", "Acme_Ops") in t
    assert ("references", "role", "Acme_Reps") in t


def test_sharingrule_territory_stays_attr_only(tmp_path):
    xml = (
        f'<?xml version="1.0"?><SharingRules {NS}>'
        "<sharingCriteriaRules><fullName>Acme_Terr</fullName>"
        "<sharedTo><territory>Acme_West</territory></sharedTo></sharingCriteriaRules>"
        "</SharingRules>"
    )
    nodes, edges = SharingRulesExtractor().extract(_w(tmp_path / "MeterPoint__c.sharingRules-meta.xml", xml))
    # territory has no node kind -> no principal edge, but kept as attr
    assert not any(e["to_name"] == "Acme_West" for e in edges)
    assert nodes[0]["shared_to"] == "Acme_West"


# rules: duplicate -> matching rule; assignment -> queue + emailtemplate
def test_duplicate_rule_references_matching_rule(tmp_path):
    xml = (
        f'<?xml version="1.0"?><DuplicateRule {NS}>'
        "<fullName>MeterPoint__c.Acme_Dupe</fullName>"
        "<duplicateRuleMatchRules><matchRuleSObjectType>MeterPoint__c</matchRuleSObjectType>"
        "<matchingRules>Acme_Match</matchingRules></duplicateRuleMatchRules>"
        "</DuplicateRule>"
    )
    _, edges = rl.RuleExtractor().extract(_w(tmp_path / "MeterPoint__c.Acme_Dupe.duplicateRule-meta.xml", xml))
    assert ("references", "matchingrule", "MeterPoint__c.Acme_Match") in _triples(edges)


def test_assignment_rule_queue_and_template(tmp_path):
    xml = (
        f'<?xml version="1.0"?><AssignmentRules {NS}>'
        "<assignmentRule><fullName>Acme_Route</fullName><ruleEntries>"
        "<assignedTo>Acme_Cases</assignedTo><assignedToType>Queue</assignedToType>"
        "<template>Acme_Notify</template>"
        "<criteriaItems><field>Lead.Country</field><operation>equals</operation>"
        "<value>SECRET_VALUE</value></criteriaItems>"
        "</ruleEntries></assignmentRule></AssignmentRules>"
    )
    nodes, edges = rl.RuleExtractor().extract(_w(tmp_path / "Lead.assignmentRules-meta.xml", xml))
    t = _triples(edges)
    assert ("references", "queue", "Acme_Cases") in t
    assert ("uses", "emailtemplate", "Acme_Notify") in t
    assert "SECRET_VALUE" not in (repr(nodes) + repr(edges))   # value never leaks


def test_escalation_rule_queue_target(tmp_path):
    xml = (
        f'<?xml version="1.0"?><EscalationRules {NS}>'
        "<escalationRule><fullName>Acme_Esc</fullName><ruleEntries>"
        "<escalationAction><assignedTo>Acme_Tier2</assignedTo>"
        "<assignedToType>Queue</assignedToType></escalationAction>"
        "</ruleEntries></escalationRule></EscalationRules>"
    )
    _, edges = rl.RuleExtractor().extract(_w(tmp_path / "Case.escalationRules-meta.xml", xml))
    assert ("references", "queue", "Acme_Tier2") in _triples(edges)


# approval processes: emailTemplate -> emailtemplate; queue approver -> queue
def test_approval_email_template_and_queue_approver(tmp_path):
    xml = (
        f'<?xml version="1.0"?><ApprovalProcess {NS}>'
        "<emailTemplate>Acme_Approval_Request</emailTemplate>"
        "<approvalStep><assignedApprover><approver>"
        "<type>queue</type><name>Acme_Approvers</name>"
        "</approver></assignedApprover></approvalStep>"
        "</ApprovalProcess>"
    )
    p = _w(tmp_path / "MeterPoint__c.Acme_Approve.approvalProcess-meta.xml", xml)
    _, edges = ApprovalProcessExtractor().extract(p)
    t = _triples(edges)
    assert ("uses", "emailtemplate", "Acme_Approval_Request") in t
    assert ("references", "queue", "Acme_Approvers") in t


# flows: dynamicChoiceSets -> reads object/field; record variable -> touches
def test_flow_dynamic_choice_set_and_record_variable(tmp_path):
    xml = (
        f'<?xml version="1.0"?><Flow {NS}>'
        "<dynamicChoiceSets><name>MeterChoices</name><object>MeterPoint__c</object>"
        "<displayField>Name</displayField><valueField>Status__c</valueField></dynamicChoiceSets>"
        "<variables><name>recVar</name><dataType>SObject</dataType>"
        "<objectType>Globex__c</objectType></variables>"
        "</Flow>"
    )
    _, edges = flows.FlowExtractor().extract(_w(tmp_path / "Acme_Flow.flow-meta.xml", xml))
    t = _triples(edges)
    assert ("reads", "object", "MeterPoint__c") in t
    assert ("reads", "field", "MeterPoint__c.Name") in t
    assert ("reads", "field", "MeterPoint__c.Status__c") in t
    assert ("touches", "object", "Globex__c") in t          # record-typed variable


# apex: custom-label references -> uses label (comments excluded)
def test_apex_custom_label_edges(tmp_path):
    src = (
        "public class AcmeLabels {\n"
        "  public String a() { return System.Label.Acme_Welcome; }\n"
        "  public String b() { return Label.Acme_Bye; }\n"
        "  // commented out: System.Label.Should_Not_Appear\n"
        "}\n"
    )
    nodes, edges = ApexExtractor().extract(_w(tmp_path / "AcmeLabels.cls", src))
    t = _triples(edges)
    assert ("uses", "label", "Acme_Welcome") in t
    assert ("uses", "label", "Acme_Bye") in t
    assert not any(name == "Should_Not_Appear" for _e, _k, name in t)
