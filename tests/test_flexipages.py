"""FlexiPage extractor tests."""
from graphbuilder import core, resolvers
from graphbuilder.extractors.flexipages import EXTRACTORS, FlexiPageExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _flexipage_xml(sobject, components):
    region = "".join(
        f"""
        <itemInstances>
          <componentInstance>
            <componentName>{c}</componentName>
          </componentInstance>
        </itemInstances>"""
        for c in components
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexiPage {NS}>
  <masterLabel>Acme MeterPoint Record Page</masterLabel>
  <sobjectType>{sobject}</sobjectType>
  <flexiPageRegions>{region}
  </flexiPageRegions>
  <type>RecordPage</type>
</FlexiPage>
"""


def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], FlexiPageExtractor)
    assert EXTRACTORS[0].source == "salesforce"


def test_handles_only_flexipages():
    ex = FlexiPageExtractor()
    assert ex.handles(__import__("pathlib").Path("AcmeMeterPoint.flexipage-meta.xml"))
    assert not ex.handles(__import__("pathlib").Path("AcmeService.cls"))
    assert not ex.handles(__import__("pathlib").Path("AcmeTrigger.trigger"))


def test_extract_nodes_and_edges(tmp_path):
    p = tmp_path / "AcmeMeterPoint.flexipage-meta.xml"
    # two custom components (c:...) plus one standard component that is skipped
    _w(p, _flexipage_xml(
        "MeterPoint__c",
        ["c:meterPointDetail", "c:acmeUsageChart", "flexipage:availableForAllPageTypes"],
    ))
    nodes, edges = FlexiPageExtractor().extract(p)

    # flexipage node (page-level attrs from the fixture XML)
    assert nodes == [{"id": "flexipage/AcmeMeterPoint",
                      "type": "flexipage", "label": "AcmeMeterPoint",
                      "page_type": "RecordPage",
                      "master_label": "Acme MeterPoint Record Page"}]

    # page-for -> object
    assert {"src": "flexipage/AcmeMeterPoint", "type": "page-for",
            "to_kind": "object", "to_name": "MeterPoint__c"} in edges

    # embeds -> lwc (only the c: custom components, standard one skipped)
    embeds = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("embeds", "lwc", "meterPointDetail") in embeds
    assert ("embeds", "lwc", "acmeUsageChart") in embeds
    assert all(tk == "lwc" or tn != "availableForAllPageTypes"
               for _, tk, tn in embeds)

    # exactly: 1 page-for + 2 embeds
    assert len(edges) == 3


def test_extract_no_object_no_page_for_edge(tmp_path):
    # App/Home pages have no <sobjectType> — must not emit a page-for edge
    p = tmp_path / "AcmeHome.flexipage-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexiPage {NS}>
  <masterLabel>Acme Home</masterLabel>
  <type>HomePage</type>
  <flexiPageRegions>
    <itemInstances>
      <componentInstance><componentName>c:acmeHomeBanner</componentName></componentInstance>
    </itemInstances>
  </flexiPageRegions>
</FlexiPage>
""")
    nodes, edges = FlexiPageExtractor().extract(p)
    assert nodes[0]["id"] == "flexipage/AcmeHome"
    assert all(e["type"] != "page-for" for e in edges)
    assert {"src": "flexipage/AcmeHome", "type": "embeds",
            "to_kind": "lwc", "to_name": "acmeHomeBanner"} in edges


def test_broken_xml_is_skipped_not_raised(tmp_path):
    # malformed XML must not raise — just a bare flexipage node, no edges
    p = tmp_path / "AcmeBroken.flexipage-meta.xml"
    _w(p, f"<FlexiPage {NS}><sobjectType>Acme__c</sobjectType")  # unterminated
    nodes, edges = FlexiPageExtractor().extract(p)
    assert nodes[0]["id"] == "flexipage/AcmeBroken"
    assert edges == []


def test_build_graph_in_isolation(tmp_path):
    p = tmp_path / "AcmeMeterPoint.flexipage-meta.xml"
    _w(p, _flexipage_xml("MeterPoint__c", ["c:meterPointDetail"]))

    g = (core.GraphBuilder()
         .register(FlexiPageExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    ids = {n["id"]: n for n in g["nodes"]}
    assert "flexipage/AcmeMeterPoint" in ids
    # page-for target is a standard-or-absent object -> resolves to an external stub
    assert ids["object/MeterPoint__c"].get("external") is True
    assert ids["lwc/meterPointDetail"].get("external") is True
    assert any(e["type"] == "page-for" and e["src"] == "flexipage/AcmeMeterPoint"
               and e["dst"] == "object/MeterPoint__c" for e in g["edges"])
    assert any(e["type"] == "embeds" and e["dst"] == "lwc/meterPointDetail"
               for e in g["edges"])
    assert g["errors"] == [] and g["unresolved"] == []

def test_managed_namespace_components(tmp_path):
    # Managed-package components ("ns:comp") become ns__comp lwc refs; platform
    # namespaces (flexipage:, lst:, notes:, runtime_*:) are skipped; local c:
    # is unchanged.
    p = tmp_path / "AcmePackaged.flexipage-meta.xml"
    _w(p, _flexipage_xml(
        "MeterPoint__c",
        [
            "c:meterPointDetail",
            "acme_pkg:cardCanvas",
            "flexipage:availableForAllPageTypes",
            "runtime_sales_activities:activityPanel",
            "lst:dynamicRelatedList",
            "notes:notesPanel",
        ],
    ))
    _, edges = FlexiPageExtractor().extract(p)
    lwcs = {e["to_name"] for e in edges if e["to_kind"] == "lwc"}
    assert lwcs == {"meterPointDetail", "acme_pkg__cardCanvas"}


# --------------------------------------------------------------------------- #
# deep parse: fields, visibility rules, actions, related lists, page attrs
# --------------------------------------------------------------------------- #
_DEEP_PAGE = f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexiPage {NS}>
  <flexiPageRegions>
    <itemInstances>
      <componentInstance>
        <componentInstanceProperties>
          <name>actionNames</name>
          <valueList>
            <valueListItems><value>Edit</value></valueListItems>
            <valueListItems><value>Delete</value></valueListItems>
            <valueListItems><value>MeterPoint__c.RecalcUsage</value></valueListItems>
            <valueListItems><value>CustomButton.MeterPoint__c.SendReading</value></valueListItems>
          </valueList>
        </componentInstanceProperties>
        <componentName>force:highlightsPanel</componentName>
      </componentInstance>
    </itemInstances>
    <mode>Replace</mode>
    <name>header</name>
    <type>Region</type>
  </flexiPageRegions>
  <flexiPageRegions>
    <itemInstances>
      <fieldInstance>
        <fieldInstanceProperties>
          <name>uiBehavior</name>
          <value>none</value>
        </fieldInstanceProperties>
        <fieldItem>Record.UsageRate__c</fieldItem>
      </fieldInstance>
    </itemInstances>
    <itemInstances>
      <fieldInstance>
        <fieldItem>Record.Name</fieldItem>
      </fieldInstance>
    </itemInstances>
    <itemInstances>
      <fieldInstance>
        <fieldItem>Record.Supplier__r.Name</fieldItem>
      </fieldInstance>
    </itemInstances>
    <itemInstances>
      <fieldInstance>
        <fieldItem>Record.UsageRate__c</fieldItem>
        <visibilityRule>
          <criteria>
            <leftValue>{{!Record.Status__c}}</leftValue>
            <operator>EQUAL</operator>
            <rightValue>Active</rightValue>
          </criteria>
          <criteria>
            <leftValue>{{!Record.Supplier__r.Status__c}}</leftValue>
            <operator>EQUAL</operator>
            <rightValue>Active</rightValue>
          </criteria>
        </visibilityRule>
      </fieldInstance>
    </itemInstances>
    <name>main</name>
    <type>Region</type>
  </flexiPageRegions>
  <flexiPageRegions>
    <itemInstances>
      <componentInstance>
        <componentInstanceProperties>
          <name>parentFieldApiName</name>
          <value>MeterPoint__c.Id</value>
        </componentInstanceProperties>
        <componentInstanceProperties>
          <name>relatedListApiName</name>
          <value>UsageReadings__r</value>
        </componentInstanceProperties>
        <componentInstanceProperties>
          <name>relatedListFieldAliases</name>
          <valueList>
            <valueListItems><value>NAME</value></valueListItems>
            <valueListItems><value>Volume__c</value></valueListItems>
          </valueList>
        </componentInstanceProperties>
        <componentName>lst:dynamicRelatedList</componentName>
      </componentInstance>
    </itemInstances>
    <itemInstances>
      <componentInstance>
        <componentInstanceProperties>
          <name>parentFieldApiName</name>
          <value>MeterPoint__c.Id</value>
        </componentInstanceProperties>
        <componentInstanceProperties>
          <name>relatedListApiName</name>
          <value>Certificates__r</value>
        </componentInstanceProperties>
        <componentName>force:relatedListSingleContainer</componentName>
      </componentInstance>
    </itemInstances>
    <name>sidebar</name>
    <type>Region</type>
  </flexiPageRegions>
  <masterLabel>Acme MeterPoint Deep Page</masterLabel>
  <sobjectType>MeterPoint__c</sobjectType>
  <template>
    <name>flexipage:recordHomeTemplateDesktop</name>
  </template>
  <type>RecordPage</type>
</FlexiPage>
"""


def test_field_instances_emit_qualified_reads(tmp_path):
    p = tmp_path / "AcmeDeep.flexipage-meta.xml"
    _w(p, _DEEP_PAGE)
    _, edges = FlexiPageExtractor().extract(p)
    reads = {e["to_name"] for e in edges if e["type"] == "reads"}
    # fieldItem refs qualified by the page object; the duplicate UsageRate__c
    # dedupes; the cross-object span (Record.Supplier__r.Name) is skipped.
    assert "MeterPoint__c.UsageRate__c" in reads
    assert "MeterPoint__c.Name" in reads
    assert all(".Supplier__r" not in r and "Supplier__r." not in r for r in reads)
    assert sum(1 for e in edges if e["type"] == "reads"
               and e["to_name"] == "MeterPoint__c.UsageRate__c") == 1
    # all reads targets are field kind and properly qualified Object.Field
    assert all(e["to_kind"] == "field" and e["to_name"].count(".") == 1
               for e in edges if e["type"] == "reads")


def test_visibility_rule_field_read(tmp_path):
    p = tmp_path / "AcmeDeep.flexipage-meta.xml"
    _w(p, _DEEP_PAGE)
    _, edges = FlexiPageExtractor().extract(p)
    reads = {e["to_name"] for e in edges if e["type"] == "reads"}
    # {!Record.Status__c} criteria -> qualified field read; the cross-object
    # criteria ({!Record.Supplier__r.Status__c}) is skipped.
    assert "MeterPoint__c.Status__c" in reads


def test_action_names_quickaction_refs(tmp_path):
    p = tmp_path / "AcmeDeep.flexipage-meta.xml"
    _w(p, _DEEP_PAGE)
    _, edges = FlexiPageExtractor().extract(p)
    uses = {e["to_name"] for e in edges if e["type"] == "uses"}
    # Only the Object.Action QuickAction ref: bare standard actions
    # (Edit/Delete) and CustomButton.* entries are skipped.
    assert uses == {"MeterPoint__c.RecalcUsage"}
    assert all(e["to_kind"] == "quickaction" for e in edges if e["type"] == "uses")


def test_platform_action_list_quickaction(tmp_path):
    # the older platformActionList shape declares actionType explicitly, so
    # even bare (global) QuickAction names are unambiguous
    p = tmp_path / "AcmeActions.flexipage-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexiPage {NS}>
  <masterLabel>Acme Actions</masterLabel>
  <platformActionList>
    <actionListContext>Record</actionListContext>
    <platformActionListItems>
      <actionName>LogAcmeReading</actionName>
      <actionType>QuickAction</actionType>
      <sortOrder>0</sortOrder>
    </platformActionListItems>
    <platformActionListItems>
      <actionName>AcmeWebLink</actionName>
      <actionType>CustomButton</actionType>
      <sortOrder>1</sortOrder>
    </platformActionListItems>
  </platformActionList>
  <sobjectType>MeterPoint__c</sobjectType>
  <type>RecordPage</type>
</FlexiPage>
""")
    _, edges = FlexiPageExtractor().extract(p)
    uses = {e["to_name"] for e in edges if e["type"] == "uses"}
    assert uses == {"LogAcmeReading"}


def test_related_list_capture(tmp_path):
    p = tmp_path / "AcmeDeep.flexipage-meta.xml"
    _w(p, _DEEP_PAGE)
    nodes, edges = FlexiPageExtractor().extract(p)
    # related-list API names land on the node (sorted) — there is no safe edge
    # target because a relationship name does not identify the related object
    assert nodes[0]["related_lists"] == ["Certificates__r", "UsageReadings__r"]
    reads = {e["to_name"] for e in edges if e["type"] == "reads"}
    # parentFieldApiName is explicitly qualified -> reads edge (deduped across
    # the two related lists); unqualified column aliases are NOT emitted
    assert "MeterPoint__c.Id" in reads
    assert sum(1 for e in edges if e["type"] == "reads"
               and e["to_name"] == "MeterPoint__c.Id") == 1
    tails = {r.split(".", 1)[-1] for r in reads}
    assert "Volume__c" not in tails and "NAME" not in tails
    # the lst:/force: related-list components are platform — no lwc embeds
    assert all(e["type"] != "embeds" for e in edges)


def test_page_type_and_template_attrs(tmp_path):
    p = tmp_path / "AcmeDeep.flexipage-meta.xml"
    _w(p, _DEEP_PAGE)
    nodes, _ = FlexiPageExtractor().extract(p)
    n = nodes[0]
    # the FlexiPage-level <type>, not a region's <type>Region</type>
    assert n["page_type"] == "RecordPage"
    assert n["template"] == "flexipage:recordHomeTemplateDesktop"
    assert n["master_label"] == "Acme MeterPoint Deep Page"


def test_apppage_without_sobject_emits_no_field_edges(tmp_path):
    # a page with no <sobjectType> cannot qualify bare Record.X refs — it must
    # emit NO reads edges at all (never a `field/.Name` garbage target)
    p = tmp_path / "AcmeApp.flexipage-meta.xml"
    _w(p, f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexiPage {NS}>
  <flexiPageRegions>
    <itemInstances>
      <fieldInstance>
        <fieldItem>Record.Name</fieldItem>
        <visibilityRule>
          <criteria>
            <leftValue>{{!Record.Status__c}}</leftValue>
            <operator>EQUAL</operator>
            <rightValue>Active</rightValue>
          </criteria>
        </visibilityRule>
      </fieldInstance>
    </itemInstances>
    <name>main</name>
    <type>Region</type>
  </flexiPageRegions>
  <masterLabel>Acme App Page</masterLabel>
  <type>AppPage</type>
</FlexiPage>
""")
    nodes, edges = FlexiPageExtractor().extract(p)
    assert nodes[0]["page_type"] == "AppPage"
    assert all(e["type"] != "reads" for e in edges)
    assert all(not e["to_name"].startswith(".") for e in edges)


def test_quickaction_ref_resolves_to_real_node(tmp_path):
    # build flexipage + quickaction together: the uses edge must land on the
    # REAL quickaction node (file-stem keyed), not an external stub
    from graphbuilder.extractors.quickactions import QuickActionExtractor

    _w(tmp_path / "flexipages" / "AcmeDeep.flexipage-meta.xml", _DEEP_PAGE)
    _w(tmp_path / "quickActions" / "MeterPoint__c.RecalcUsage.quickAction-meta.xml",
       f"""<?xml version="1.0" encoding="UTF-8"?>
<QuickAction {NS}>
  <label>Recalc Usage</label>
  <optionsCreateFeedItem>false</optionsCreateFeedItem>
  <type>LightningWebComponent</type>
  <lightningWebComponent>recalcUsage</lightningWebComponent>
</QuickAction>
""")

    g = (core.GraphBuilder()
         .register(FlexiPageExtractor(), QuickActionExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    ids = {n["id"]: n for n in g["nodes"]}
    qa = ids["quickaction/MeterPoint__c.RecalcUsage"]
    assert not qa.get("external")
    assert any(e["type"] == "uses"
               and e["src"] == "flexipage/AcmeDeep"
               and e["dst"] == "quickaction/MeterPoint__c.RecalcUsage"
               for e in g["edges"])
    # bare standard actions never became stub quickaction nodes
    assert "quickaction/Edit" not in ids and "quickaction/Delete" not in ids
    assert g["errors"] == [] and g["unresolved"] == []
