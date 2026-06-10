"""Flows extractor tests."""
from graphbuilder import core, resolvers
from graphbuilder.extractors import flows

# Exercises every emitted node/edge type: touches->object, calls->apexclass,
# flowelement nodes + contains, subflow->flow, invocable->apexmethod/apexclass,
# reads/writes->object.
FLOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Acme Meter Onboarding</label>
    <processType>AutoLaunchedFlow</processType>
    <start>
        <object>MeterPoint__c</object>
    </start>
    <recordLookups>
        <name>Get_Account</name>
        <label>Get Account</label>
        <object>Account</object>
        <queriedFields>Id</queriedFields>
        <queriedFields>Name</queriedFields>
        <queriedFields>Industry__c</queriedFields>
        <filters>
            <field>BillingCity</field>
            <operator>EqualTo</operator>
        </filters>
        <filters>
            <field>Active__c</field>
            <operator>EqualTo</operator>
        </filters>
    </recordLookups>
    <recordCreates>
        <name>Create_Reading</name>
        <label>Create Reading</label>
        <object>Reading__c</object>
        <inputAssignments>
            <field>Value__c</field>
        </inputAssignments>
        <inputAssignments>
            <field>MeterPoint__c</field>
        </inputAssignments>
    </recordCreates>
    <recordUpdates>
        <name>Update_Meter</name>
        <label>Update Meter</label>
        <inputReference>varMeter</inputReference>
        <inputAssignments>
            <field>Status__c</field>
        </inputAssignments>
    </recordUpdates>
    <recordUpdates>
        <name>Update_Account</name>
        <label>Update Account</label>
        <object>Account</object>
        <inputAssignments>
            <field>LastReadDate__c</field>
        </inputAssignments>
    </recordUpdates>
    <recordDeletes>
        <name>Delete_Stale</name>
        <label>Delete Stale</label>
        <object>StaleReading__c</object>
    </recordDeletes>
    <decisions>
        <name>Is_Active</name>
        <label>Is Active?</label>
        <rules>
            <name>Active_Rule</name>
            <conditions>
                <leftValueReference>$Record.Active__c</leftValueReference>
                <operator>EqualTo</operator>
            </conditions>
            <conditions>
                <leftValueReference>Record.Status__c</leftValueReference>
                <operator>EqualTo</operator>
            </conditions>
            <conditions>
                <leftValueReference>varMeter.Reading__c</leftValueReference>
                <operator>GreaterThan</operator>
            </conditions>
            <conditions>
                <leftValueReference>$Record.Owner__r.Name</leftValueReference>
                <operator>IsNull</operator>
            </conditions>
        </rules>
    </decisions>
    <assignments>
        <name>Set_Status</name>
        <label>Set Status</label>
    </assignments>
    <screens>
        <name>Confirm_Screen</name>
        <label>Confirm</label>
    </screens>
    <subflows>
        <name>Run_Billing</name>
        <label>Run Billing</label>
        <flowName>Acme_Billing_Subflow</flowName>
    </subflows>
    <actionCalls>
        <name>Compute_Tariff</name>
        <label>Compute Tariff</label>
        <actionType>apex</actionType>
        <actionName>AcmeTariffCalculator</actionName>
    </actionCalls>
    <actionCalls>
        <name>Notify_Method</name>
        <label>Notify</label>
        <actionType>apex</actionType>
        <actionName>AcmeNotifier.send</actionName>
    </actionCalls>
    <actionCalls>
        <name>Send_Email</name>
        <label>Send Email</label>
        <actionType>emailSimple</actionType>
        <actionName>emailSimple</actionName>
    </actionCalls>
</Flow>
"""


def _write_flow(tmp_path, name, xml):
    flows_dir = tmp_path / "force-app" / "main" / "default" / "flows"
    flows_dir.mkdir(parents=True, exist_ok=True)
    p = flows_dir / f"{name}.flow-meta.xml"
    p.write_text(xml, "utf-8")
    return p


def test_extract_nodes_and_edges(tmp_path):
    p = _write_flow(tmp_path, "Acme_Meter_Onboarding", FLOW_XML)
    nodes, edges = flows.FlowExtractor().extract(p)

    nids = {n["id"] for n in nodes}
    by_id = {n["id"]: n for n in nodes}

    # flow node
    assert "flow/Acme_Meter_Onboarding" in nids
    assert by_id["flow/Acme_Meter_Onboarding"]["type"] == "flow"
    assert by_id["flow/Acme_Meter_Onboarding"]["process_type"] == "AutoLaunchedFlow"

    # flowelement nodes (one per named element)
    expected_elements = {
        "Get_Account", "Create_Reading", "Update_Meter", "Update_Account",
        "Delete_Stale", "Is_Active", "Set_Status", "Confirm_Screen",
        "Run_Billing", "Compute_Tariff", "Notify_Method", "Send_Email",
    }
    for e in expected_elements:
        eid = f"flowelement/Acme_Meter_Onboarding.{e}"
        assert eid in nids, eid
        assert by_id[eid]["type"] == "flowelement"
    assert by_id["flowelement/Acme_Meter_Onboarding.Get_Account"]["element_type"] == "recordLookups"

    # edges (raw, target named by (to_kind, to_name))
    def has(src, etype, to_kind, to_name):
        return any(
            ed["src"] == src and ed["type"] == etype
            and ed["to_kind"] == to_kind and ed["to_name"] == to_name
            for ed in edges
        )

    fid = "flow/Acme_Meter_Onboarding"

    # touches -> object (collected from start + record elements with <object>)
    assert has(fid, "touches", "object", "MeterPoint__c")
    assert has(fid, "touches", "object", "Account")
    assert has(fid, "touches", "object", "Reading__c")
    assert has(fid, "touches", "object", "StaleReading__c")

    # calls -> apexclass (apex action call, class-level name)
    assert has(fid, "calls", "apexclass", "AcmeTariffCalculator")

    # contains: flow -> each flowelement
    for e in expected_elements:
        assert has(fid, "contains", "flowelement", f"Acme_Meter_Onboarding.{e}")

    # subflow: flow -> flow
    assert has(fid, "subflow", "flow", "Acme_Billing_Subflow")

    # invocable: apex actionCall element -> apexclass (no dot) / apexmethod (dotted)
    assert has("flowelement/Acme_Meter_Onboarding.Compute_Tariff",
               "invocable", "apexclass", "AcmeTariffCalculator")
    assert has("flowelement/Acme_Meter_Onboarding.Notify_Method",
               "invocable", "apexmethod", "AcmeNotifier.send")

    # reads / writes: record element -> object
    assert has("flowelement/Acme_Meter_Onboarding.Get_Account", "reads", "object", "Account")
    assert has("flowelement/Acme_Meter_Onboarding.Create_Reading", "writes", "object", "Reading__c")
    assert has("flowelement/Acme_Meter_Onboarding.Delete_Stale", "writes", "object", "StaleReading__c")
    # recordUpdates by inputReference has no <object> -> no write edge, but element node exists
    assert not has("flowelement/Acme_Meter_Onboarding.Update_Meter", "writes", "object", "varMeter")

    # field-level fidelity
    ga = "flowelement/Acme_Meter_Onboarding.Get_Account"
    # recordLookups queriedFields -> reads -> field (Object.Field)
    assert has(ga, "reads", "field", "Account.Id")
    assert has(ga, "reads", "field", "Account.Name")
    assert has(ga, "reads", "field", "Account.Industry__c")
    # recordLookups filter-condition fields -> reads -> field
    assert has(ga, "reads", "field", "Account.BillingCity")
    assert has(ga, "reads", "field", "Account.Active__c")

    # recordCreates inputAssignments <field> -> writes -> field
    cr = "flowelement/Acme_Meter_Onboarding.Create_Reading"
    assert has(cr, "writes", "field", "Reading__c.Value__c")
    assert has(cr, "writes", "field", "Reading__c.MeterPoint__c")

    # recordUpdates inputAssignments <field> -> writes -> field (object-bound update)
    ua = "flowelement/Acme_Meter_Onboarding.Update_Account"
    assert has(ua, "writes", "object", "Account")
    assert has(ua, "writes", "field", "Account.LastReadDate__c")

    # recordUpdates with only an inputReference (no <object>) emits NO field edge
    assert not any(ed["src"] == "flowelement/Acme_Meter_Onboarding.Update_Meter"
                   and ed["type"] == "writes" and ed["to_kind"] == "field"
                   for ed in edges)

    # decision conditions: Record.Field / $Record.Field -> reads -> field
    # object context comes from the record-triggered start <object> (MeterPoint__c)
    ia = "flowelement/Acme_Meter_Onboarding.Is_Active"
    assert has(ia, "reads", "field", "MeterPoint__c.Active__c")   # $Record.Active__c
    assert has(ia, "reads", "field", "MeterPoint__c.Status__c")   # Record.Status__c
    # element merge field (varMeter.Reading__c) is NOT a record field -> skipped
    assert not has(ia, "reads", "field", "MeterPoint__c.Reading__c")
    assert not any(ed["src"] == ia and ed["to_name"] == "varMeter.Reading__c" for ed in edges)
    # multi-hop relationship traversal ($Record.Owner__r.Name) -> skipped
    assert not any(ed["src"] == ia and ed["type"] == "reads"
                   and ed["to_kind"] == "field" and ".Owner__r" in ed["to_name"]
                   for ed in edges)

    # non-apex action calls do not emit an invocable edge
    assert not any(ed["src"] == "flowelement/Acme_Meter_Onboarding.Send_Email"
                   and ed["type"] == "invocable" for ed in edges)


def test_build_graph_resolves_and_stubs(tmp_path):
    _write_flow(tmp_path, "Acme_Meter_Onboarding", FLOW_XML)
    g = (core.GraphBuilder()
         .register(flows.FlowExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    by_id = {n["id"]: n for n in g["nodes"]}
    assert "flow/Acme_Meter_Onboarding" in by_id

    # touched object resolved to an EXTERNAL stub (not in this synthetic repo)
    assert by_id["object/MeterPoint__c"].get("external") is True

    # subflow target is a stub flow node, wired by a resolved edge
    assert by_id["flow/Acme_Billing_Subflow"].get("external") is True
    assert any(e["type"] == "subflow" and e["src"] == "flow/Acme_Meter_Onboarding"
               and e["dst"] == "flow/Acme_Billing_Subflow" for e in g["edges"])

    # invocable apexmethod edge resolves to its stub node id
    assert any(e["type"] == "invocable"
               and e["dst"] == "apexmethod/AcmeNotifier.send" for e in g["edges"])

    # field-level reads/writes resolve to external field stubs and wire up
    assert by_id["field/Account.Industry__c"].get("external") is True
    assert any(e["type"] == "reads"
               and e["src"] == "flowelement/Acme_Meter_Onboarding.Get_Account"
               and e["dst"] == "field/Account.Industry__c" for e in g["edges"])
    assert any(e["type"] == "writes"
               and e["src"] == "flowelement/Acme_Meter_Onboarding.Create_Reading"
               and e["dst"] == "field/Reading__c.Value__c" for e in g["edges"])
    assert any(e["type"] == "reads"
               and e["src"] == "flowelement/Acme_Meter_Onboarding.Is_Active"
               and e["dst"] == "field/MeterPoint__c.Active__c" for e in g["edges"])

    # everything wired through stub resolvers — including `emailalert` (vocab
    # kind; the alert itself is org metadata we don't parse, so it stubs)
    assert g["errors"] == [] and g["unresolved"] == []
    assert by_id["emailalert/emailSimple"].get("external") is True
    assert any(e["type"] == "uses"
               and e["src"] == "flowelement/Acme_Meter_Onboarding.Send_Email"
               and e["dst"] == "emailalert/emailSimple" for e in g["edges"])


def test_lwc_name_namespace_handling():
    # default namespace `c` is stripped -> bare local name (keyed lwc/<name>)
    assert flows._lwc_name("c:acmeCard") == "acmeCard"
    assert flows._lwc_name("c__acmeCard") == "acmeCard"
    assert flows._lwc_name("acmeCard") == "acmeCard"
    # a real managed-package namespace is preserved in API form so the edge
    # targets the packaged component instead of colliding with a local one
    assert flows._lwc_name("vlocity_cmt:flexCard") == "vlocity_cmt__flexCard"
    assert flows._lwc_name("vlocity_cmt__flexCard") == "vlocity_cmt__flexCard"
    # junk tokens still rejected
    assert flows._lwc_name("a b") is None
    assert flows._lwc_name("c:") is None


def test_broken_xml_does_not_raise(tmp_path):
    # malformed XML must not raise; base node still emitted from the filename
    p = _write_flow(tmp_path, "Acme_Broken", "<Flow><not closed")
    nodes, edges = flows.FlowExtractor().extract(p)
    assert any(n["id"] == "flow/Acme_Broken" for n in nodes)


def test_handles():
    ex = flows.FlowExtractor()
    assert ex.handles(__import__("pathlib").Path("x/Acme.flow-meta.xml")) is True
    assert ex.handles(__import__("pathlib").Path("x/Acme.trigger")) is False


def test_email_action_uses_edge(tmp_path):
    """emailSimple actionCall -> uses -> emailalert (name only)."""
    p = _write_flow(tmp_path, "Acme_Meter_Onboarding", FLOW_XML)
    _nodes, edges = flows.FlowExtractor().extract(p)
    assert any(
        ed["src"] == "flowelement/Acme_Meter_Onboarding.Send_Email"
        and ed["type"] == "uses" and ed["to_kind"] == "emailalert"
        and ed["to_name"] == "emailSimple"
        for ed in edges
    )
    # apex / non-email actions never emit a uses->emailalert edge
    assert not any(
        ed["src"] == "flowelement/Acme_Meter_Onboarding.Compute_Tariff"
        and ed["type"] == "uses" for ed in edges
    )


# A record-triggered flow whose screen embeds an LWC.
RECORD_SCREEN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Acme Record Screen</label>
    <processType>Flow</processType>
    <start>
        <object>MeterPoint__c</object>
        <recordTriggerType>Create</recordTriggerType>
        <triggerType>RecordAfterSave</triggerType>
    </start>
    <screens>
        <name>Reading_Screen</name>
        <label>Reading</label>
        <fields>
            <name>Heading</name>
            <fieldType>DisplayText</fieldType>
        </fields>
        <fields>
            <name>Panel</name>
            <fieldType>ComponentInstance</fieldType>
            <extensionName>c:meterReadingPanel</extensionName>
        </fields>
        <fields>
            <name>Section</name>
            <fieldType>RegionContainer</fieldType>
            <fields>
                <name>Nested</name>
                <fieldType>ComponentInstance</fieldType>
                <extensionName>c__globexChart</extensionName>
            </fields>
        </fields>
    </screens>
    <actionCalls>
        <name>Alert_Manager</name>
        <label>Alert</label>
        <actionType>emailAlert</actionType>
        <actionName>MeterPoint__c.Overdue_Alert</actionName>
    </actionCalls>
</Flow>
"""


def test_screen_embeds_lwc_and_record_trigger(tmp_path):
    p = _write_flow(tmp_path, "Acme_Record_Screen", RECORD_SCREEN_XML)
    nodes, edges = flows.FlowExtractor().extract(p)
    by_id = {n["id"]: n for n in nodes}

    # flow classified as record-triggered, not scheduled
    fnode = by_id["flow/Acme_Record_Screen"]
    assert fnode["trigger_type"] == "record"
    assert "schedule" not in fnode

    sid = "flowelement/Acme_Record_Screen.Reading_Screen"

    def has(src, etype, to_kind, to_name):
        return any(
            ed["src"] == src and ed["type"] == etype
            and ed["to_kind"] == to_kind and ed["to_name"] == to_name
            for ed in edges
        )

    # screen field components embed LWCs (namespace prefix dropped, name only)
    assert has(sid, "embeds", "lwc", "meterReadingPanel")   # c:meterReadingPanel
    assert has(sid, "embeds", "lwc", "globexChart")         # c__globexChart (nested)
    # plain display-text field is not a component -> no embeds for it
    embeds = [ed for ed in edges if ed["type"] == "embeds"]
    assert {ed["to_name"] for ed in embeds} == {"meterReadingPanel", "globexChart"}

    # emailAlert actionCall -> uses -> emailalert (name only)
    assert has("flowelement/Acme_Record_Screen.Alert_Manager",
               "uses", "emailalert", "MeterPoint__c.Overdue_Alert")


def test_screen_embeds_resolve_to_lwc_stub(tmp_path):
    _write_flow(tmp_path, "Acme_Record_Screen", RECORD_SCREEN_XML)
    g = (core.GraphBuilder()
         .register(flows.FlowExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    by_id = {n["id"]: n for n in g["nodes"]}
    # embeds target resolves to an external lwc stub and wires up
    assert by_id["lwc/meterReadingPanel"].get("external") is True
    assert any(
        e["type"] == "embeds"
        and e["src"] == "flowelement/Acme_Record_Screen.Reading_Screen"
        and e["dst"] == "lwc/meterReadingPanel"
        for e in g["edges"]
    )


PLATFORM_EVENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Acme Event Handler</label>
    <processType>AutoLaunchedFlow</processType>
    <start>
        <object>MeterReading__e</object>
        <triggerType>PlatformEvent</triggerType>
    </start>
    <assignments>
        <name>Set_Flag</name>
        <label>Set Flag</label>
    </assignments>
</Flow>
"""

SCHEDULED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <label>Acme Nightly Sweep</label>
    <processType>AutoLaunchedFlow</processType>
    <start>
        <object>MeterPoint__c</object>
        <triggerType>Scheduled</triggerType>
        <schedule>
            <frequency>Daily</frequency>
            <startDate>2026-01-01</startDate>
        </schedule>
    </start>
    <recordLookups>
        <name>Get_Points</name>
        <label>Get Points</label>
        <object>MeterPoint__c</object>
        <queriedFields>Id</queriedFields>
    </recordLookups>
</Flow>
"""


def test_platform_event_trigger_type(tmp_path):
    p = _write_flow(tmp_path, "Acme_Event_Handler", PLATFORM_EVENT_XML)
    nodes, _edges = flows.FlowExtractor().extract(p)
    by_id = {n["id"]: n for n in nodes}
    fnode = by_id["flow/Acme_Event_Handler"]
    # start object ends with __e -> platformevent, not scheduled
    assert fnode["trigger_type"] == "platformevent"
    assert "schedule" not in fnode


def test_scheduled_trigger_type_and_schedule_attr(tmp_path):
    p = _write_flow(tmp_path, "Acme_Nightly_Sweep", SCHEDULED_XML)
    nodes, _edges = flows.FlowExtractor().extract(p)
    by_id = {n["id"]: n for n in nodes}
    fnode = by_id["flow/Acme_Nightly_Sweep"]
    # a scheduled start wins classification and flags the schedule attr
    assert fnode["trigger_type"] == "schedule"
    assert fnode["schedule"] is True


def test_autolaunched_no_start_has_no_trigger_type(tmp_path):
    # a plain autolaunched flow with no <start> gets no trigger_type/schedule
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Flow xmlns="http://soap.sforce.com/2006/04/metadata">'
        '<processType>AutoLaunchedFlow</processType>'
        '<assignments><name>A</name><label>A</label></assignments>'
        '</Flow>'
    )
    p = _write_flow(tmp_path, "Acme_Plain", xml)
    nodes, _edges = flows.FlowExtractor().extract(p)
    fnode = {n["id"]: n for n in nodes}["flow/Acme_Plain"]
    assert "trigger_type" not in fnode
    assert "schedule" not in fnode
