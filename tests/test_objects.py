"""ObjectExtractor tests."""
from pathlib import Path

from graphbuilder import core, resolvers
from graphbuilder.extractors.objects import ObjectExtractor


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _meterpoint_object(root: Path) -> Path:
    """A fictional MeterPoint__c custom object with fields, a lookup, a
    master-detail, a formula field and a validation rule."""
    obj_dir = root / "force-app" / "main" / "default" / "objects" / "MeterPoint__c"
    _w(obj_dir / "MeterPoint__c.object-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <label>Meter Point</label>\n'
       '</CustomObject>\n')

    fields = obj_dir / "fields"
    # plain number field
    _w(fields / "Reading__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Reading__c</fullName>\n'
       '  <label>Reading</label>\n'
       '  <type>Number</type>\n'
       '</CustomField>\n')
    # plain number field used by the formula and the validation rule
    _w(fields / "Multiplier__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Multiplier__c</fullName>\n'
       '  <label>Multiplier</label>\n'
       '  <type>Number</type>\n'
       '</CustomField>\n')
    # lookup to a standard object (off-repo -> external stub)
    _w(fields / "Owner__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Owner__c</fullName>\n'
       '  <label>Owner</label>\n'
       '  <type>Lookup</type>\n'
       '  <referenceTo>Account</referenceTo>\n'
       '</CustomField>\n')
    # master-detail to a fictional parent object
    _w(fields / "Site__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Site__c</fullName>\n'
       '  <label>Site</label>\n'
       '  <type>MasterDetail</type>\n'
       '  <referenceTo>AcmeSite__c</referenceTo>\n'
       '</CustomField>\n')
    # formula field referencing Reading__c and Multiplier__c, plus a cross-object
    # hop Site__r.Name (Site__c is the master-detail above -> AcmeSite__c)
    _w(fields / "Consumption__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Consumption__c</fullName>\n'
       '  <label>Consumption</label>\n'
       '  <type>Formula</type>\n'
       '  <formula>Reading__c * Multiplier__c &amp; " @ " &amp; Site__r.Name</formula>\n'
       '</CustomField>\n')
    # rollup-summary field: sum of a child object's field, keyed by a child FK
    _w(fields / "TotalUsage__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>TotalUsage__c</fullName>\n'
       '  <label>Total Usage</label>\n'
       '  <type>Summary</type>\n'
       '  <summarizedField>AcmeUsage__c.Quantity__c</summarizedField>\n'
       '  <summaryForeignKey>AcmeUsage__c.MeterPoint__c</summaryForeignKey>\n'
       '  <summaryOperation>sum</summaryOperation>\n'
       '</CustomField>\n')

    vrs = obj_dir / "validationRules"
    _w(vrs / "PositiveReading.validationRule-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<ValidationRule xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>PositiveReading</fullName>\n'
       '  <active>true</active>\n'
       '  <errorConditionFormula>Reading__c &lt; 0 || ISBLANK(Multiplier__c)</errorConditionFormula>\n'
       '  <errorMessage>Reading must be positive.</errorMessage>\n'
       '</ValidationRule>\n')
    # validation rule that hops to the parent object via the master-detail relationship
    _w(vrs / "ParentActive.validationRule-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<ValidationRule xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>ParentActive</fullName>\n'
       '  <active>true</active>\n'
       '  <errorConditionFormula>NOT(Site__r.IsActive__c) &amp;&amp; Reading__c &gt; 0</errorConditionFormula>\n'
       '  <errorMessage>Parent site must be active.</errorMessage>\n'
       '</ValidationRule>\n')
    return obj_dir / "MeterPoint__c.object-meta.xml"


def test_handles():
    ex = ObjectExtractor()
    assert ex.source == "salesforce"
    assert ex.handles(Path("objects/MeterPoint__c/MeterPoint__c.object-meta.xml"))
    assert not ex.handles(Path("objects/MeterPoint__c/fields/Reading__c.field-meta.xml"))
    assert not ex.handles(Path("triggers/MeterPointTrigger.trigger"))


def test_extract_nodes_and_edges(tmp_path):
    meta = _meterpoint_object(tmp_path)
    nodes, edges = ObjectExtractor().extract(meta)
    nbyid = {n["id"]: n for n in nodes}

    # object node carries the label
    assert nbyid["object/MeterPoint__c"]["type"] == "object"
    assert nbyid["object/MeterPoint__c"]["label"] == "Meter Point"

    # field nodes exist, each with a field_of edge to the object
    for fld in ("Reading__c", "Multiplier__c", "Owner__c", "Site__c",
                "Consumption__c", "TotalUsage__c"):
        fid = f"field/MeterPoint__c.{fld}"
        assert fid in nbyid and nbyid[fid]["type"] == "field"
        assert any(e["src"] == fid and e["type"] == "field_of"
                   and e["to_kind"] == "object" and e["to_name"] == "MeterPoint__c"
                   for e in edges)

    # lookup vs master-detail distinguished by the relationship attr
    assert nbyid["field/MeterPoint__c.Owner__c"]["relationship"] == "lookup"
    assert nbyid["field/MeterPoint__c.Site__c"]["relationship"] == "master-detail"
    assert "relationship" not in nbyid["field/MeterPoint__c.Reading__c"]

    # lookup edges field -> referenced object
    assert any(e["src"] == "field/MeterPoint__c.Owner__c" and e["type"] == "lookup"
               and e["to_kind"] == "object" and e["to_name"] == "Account" for e in edges)
    assert any(e["src"] == "field/MeterPoint__c.Site__c" and e["type"] == "lookup"
               and e["to_kind"] == "object" and e["to_name"] == "AcmeSite__c" for e in edges)

    # formula edges: Consumption__c -> own fields AND the cross-object hop
    # Site__r.Name, resolved to the related object AcmeSite__c.Name
    formula_targets = {e["to_name"] for e in edges
                       if e["type"] == "formula" and e["src"] == "field/MeterPoint__c.Consumption__c"}
    assert formula_targets == {
        "MeterPoint__c.Reading__c",
        "MeterPoint__c.Multiplier__c",
        "AcmeSite__c.Name",
    }
    # all formula edges target the field kind
    assert all(e["to_kind"] == "field" for e in edges
               if e["type"] == "formula" and e["src"] == "field/MeterPoint__c.Consumption__c")

    # validates edges: object -> bare own fields AND the relationship hop
    # Site__r.IsActive__c resolved to the parent AcmeSite__c.IsActive__c
    validates_targets = {e["to_name"] for e in edges
                         if e["type"] == "validates" and e["src"] == "object/MeterPoint__c"}
    assert validates_targets == {
        "MeterPoint__c.Reading__c",
        "MeterPoint__c.Multiplier__c",
        "AcmeSite__c.IsActive__c",
    }

    # rollup-summary reads: the summarized child field + the child object + the FK
    reads = {(e["to_kind"], e["to_name"]) for e in edges
             if e["type"] == "reads" and e["src"] == "field/MeterPoint__c.TotalUsage__c"}
    assert ("field", "AcmeUsage__c.Quantity__c") in reads        # <summarizedField>
    assert ("object", "AcmeUsage__c") in reads                   # child object
    assert ("field", "AcmeUsage__c.MeterPoint__c") in reads      # <summaryForeignKey>


def test_via_graph_builder_resolves_and_stubs(tmp_path):
    _meterpoint_object(tmp_path)
    g = (core.GraphBuilder()
         .register(ObjectExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    ids = {n["id"]: n for n in g["nodes"]}

    # everything wires up; no extractor errors, no unresolved refs
    assert g["errors"] == []
    assert g["unresolved"] == []

    # the off-repo lookup target became an external stub object
    assert "object/Account" in ids and ids["object/Account"].get("external") is True
    # the fictional master-detail parent is referenced-but-not-retrieved -> stub too
    assert "object/AcmeSite__c" in ids and ids["object/AcmeSite__c"].get("external") is True

    # field_of edge resolved to a concrete field id
    assert any(e["type"] == "field_of" and e["src"] == "field/MeterPoint__c.Consumption__c"
               and e["dst"] == "object/MeterPoint__c" for e in g["edges"])
    # formula edge resolved field -> field (both in-repo, not stubs)
    assert any(e["type"] == "formula" and e["src"] == "field/MeterPoint__c.Consumption__c"
               and e["dst"] == "field/MeterPoint__c.Reading__c" for e in g["edges"])
    assert ids["field/MeterPoint__c.Reading__c"].get("external") is not True
    # validates edge resolved object -> field
    assert any(e["type"] == "validates" and e["src"] == "object/MeterPoint__c"
               and e["dst"] == "field/MeterPoint__c.Multiplier__c" for e in g["edges"])

    # cross-object formula hop resolved field -> field on the related (stub) object
    assert any(e["type"] == "formula" and e["src"] == "field/MeterPoint__c.Consumption__c"
               and e["dst"] == "field/AcmeSite__c.Name" for e in g["edges"])
    assert ids["field/AcmeSite__c.Name"].get("external") is True
    # validation-rule relationship hop resolved object -> parent field (stub)
    assert any(e["type"] == "validates" and e["src"] == "object/MeterPoint__c"
               and e["dst"] == "field/AcmeSite__c.IsActive__c" for e in g["edges"])
    # rollup-summary reads resolved to the child field, child object, and FK
    assert any(e["type"] == "reads" and e["src"] == "field/MeterPoint__c.TotalUsage__c"
               and e["dst"] == "field/AcmeUsage__c.Quantity__c" for e in g["edges"])
    assert any(e["type"] == "reads" and e["src"] == "field/MeterPoint__c.TotalUsage__c"
               and e["dst"] == "object/AcmeUsage__c" for e in g["edges"])
    assert any(e["type"] == "reads" and e["src"] == "field/MeterPoint__c.TotalUsage__c"
               and e["dst"] == "field/AcmeUsage__c.MeterPoint__c" for e in g["edges"])


def test_no_raise_on_broken_object_xml(tmp_path):
    obj_dir = tmp_path / "objects" / "Broken__c"
    _w(obj_dir / "Broken__c.object-meta.xml", "<CustomObject><not closed")
    _w(obj_dir / "fields" / "Bad__c.field-meta.xml", "<<<not xml at all")
    _w(obj_dir / "validationRules" / "Bad.validationRule-meta.xml", "garbage")
    # must not raise; at minimum yields the object node, skipping the broken bits
    nodes, edges = ObjectExtractor().extract(obj_dir / "Broken__c.object-meta.xml")
    obj = next(n for n in nodes if n["id"] == "object/Broken__c")
    # broken xml still classifies (suffix-based) and never raises
    assert obj["category"] == "custom"


def _bare_object(root: Path, api_name: str, body: str) -> Path:
    """A minimal object folder named `api_name` whose object-meta.xml is `body`."""
    obj_dir = root / "force-app" / "main" / "default" / "objects" / api_name
    meta = obj_dir / f"{api_name}.object-meta.xml"
    _w(meta, body)
    return meta


def test_existing_custom_object_classified_custom(tmp_path):
    # the rich MeterPoint__c fixture is a plain custom object
    meta = _meterpoint_object(tmp_path)
    nodes, _ = ObjectExtractor().extract(meta)
    obj = next(n for n in nodes if n["id"] == "object/MeterPoint__c")
    assert obj["category"] == "custom"
    assert obj["label"] == "Meter Point"


def test_category_for_each_kind(tmp_path):
    cases = {
        # platform event — suffix wins
        "AcmeSignal__e": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '  <label>Acme Signal</label>\n'
            '</CustomObject>\n',
            "platformevent",
        ),
        # custom metadata type — suffix wins
        "AcmeConfig__mdt": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '  <label>Acme Config</label>\n'
            '</CustomObject>\n',
            "custommetadata",
        ),
        # custom setting — __c suffix, but <customSettingsType> takes precedence
        "GlobexSetting__c": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '  <label>Globex Setting</label>\n'
            '  <customSettingsType>Hierarchy</customSettingsType>\n'
            '</CustomObject>\n',
            "customsetting",
        ),
        # big object — suffix wins
        "AcmeLedger__b": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '  <label>Acme Ledger</label>\n'
            '</CustomObject>\n',
            "bigobject",
        ),
        # external object — suffix wins
        "AcmeOrder__x": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '  <label>Acme Order</label>\n'
            '</CustomObject>\n',
            "externalobject",
        ),
        # plain custom object
        "MeterPoint__c": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '  <label>Meter Point</label>\n'
            '</CustomObject>\n',
            "custom",
        ),
        # standard object (no managed suffix, no custom-setting marker)
        "Account": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '  <label>Account</label>\n'
            '</CustomObject>\n',
            "standard",
        ),
    }
    for api_name, (body, expected) in cases.items():
        meta = _bare_object(tmp_path, api_name, body)
        nodes, _ = ObjectExtractor().extract(meta)
        obj = next(n for n in nodes if n["id"] == f"object/{api_name}")
        assert obj["category"] == expected, (api_name, obj["category"])


def test_custom_setting_detected_in_broken_xml(tmp_path):
    # value-blind tag-presence fallback when the XML won't parse
    meta = _bare_object(
        tmp_path, "GlobexSetting__c",
        "<CustomObject><customSettingsType>List</customSettingsType><oops")
    nodes, _ = ObjectExtractor().extract(meta)
    obj = next(n for n in nodes if n["id"] == "object/GlobexSetting__c")
    assert obj["category"] == "customsetting"


def test_category_does_not_perturb_graph(tmp_path):
    # the full graph still wires cleanly with categories assigned
    _meterpoint_object(tmp_path)
    g = (core.GraphBuilder()
         .register(ObjectExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    assert g["errors"] == []
    assert g["unresolved"] == []
    ids = {n["id"]: n for n in g["nodes"]}
    assert ids["object/MeterPoint__c"]["category"] == "custom"


def _depconfig_object(root: Path) -> Path:
    """A MeterReading__c object with record types, a dependent picklist
    (controllingField) and a lookup filter."""
    obj_dir = root / "force-app" / "main" / "default" / "objects" / "MeterReading__c"
    _w(obj_dir / "MeterReading__c.object-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <label>Meter Reading</label>\n'
       '</CustomObject>\n')

    fields = obj_dir / "fields"
    # controlling picklist
    _w(fields / "Category__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Category__c</fullName>\n'
       '  <label>Category</label>\n'
       '  <type>Picklist</type>\n'
       '</CustomField>\n')
    # dependent picklist: its valueSet is controlled by Category__c
    _w(fields / "SubCategory__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>SubCategory__c</fullName>\n'
       '  <label>Sub Category</label>\n'
       '  <type>Picklist</type>\n'
       '  <valueSet>\n'
       '    <controllingField>Category__c</controllingField>\n'
       '    <valueSetDefinition>\n'
       '      <value><fullName>Alpha</fullName><label>Alpha</label></value>\n'
       '    </valueSetDefinition>\n'
       '  </valueSet>\n'
       '</CustomField>\n')
    # lookup with a filter that references fields ($Source.Region__c and a value
    # field on the target object) — but also a literal <value> we must NOT read
    _w(fields / "Inspector__c.field-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Inspector__c</fullName>\n'
       '  <label>Inspector</label>\n'
       '  <type>Lookup</type>\n'
       '  <referenceTo>GlobexInspector__c</referenceTo>\n'
       '  <lookupFilter>\n'
       '    <active>true</active>\n'
       '    <filterItems>\n'
       '      <field>GlobexInspector__c.Region__c</field>\n'
       '      <operation>equals</operation>\n'
       '      <valueField>$Source.Region__c</valueField>\n'
       '    </filterItems>\n'
       '    <filterItems>\n'
       '      <field>GlobexInspector__c.IsActive__c</field>\n'
       '      <operation>equals</operation>\n'
       '      <value>SECRET-DO-NOT-INGEST</value>\n'
       '    </filterItems>\n'
       '  </lookupFilter>\n'
       '</CustomField>\n')

    rts = obj_dir / "recordTypes"
    _w(rts / "Residential.recordType-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<RecordType xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Residential</fullName>\n'
       '  <active>true</active>\n'
       '  <label>Residential</label>\n'
       '  <picklistValues>\n'
       '    <picklist>Category__c</picklist>\n'
       '    <values><fullName>SENSITIVE_VALUE</fullName><default>false</default></values>\n'
       '  </picklistValues>\n'
       '</RecordType>\n')
    _w(rts / "Commercial.recordType-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<RecordType xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <fullName>Commercial</fullName>\n'
       '  <active>true</active>\n'
       '  <label>Commercial</label>\n'
       '</RecordType>\n')
    return obj_dir / "MeterReading__c.object-meta.xml"


def test_record_types_node_and_contains(tmp_path):
    meta = _depconfig_object(tmp_path)
    nodes, edges = ObjectExtractor().extract(meta)
    nbyid = {n["id"]: n for n in nodes}

    # one recordtype node per recordTypes/*.recordType-meta.xml, id Object.RecordType
    for rt in ("Residential", "Commercial"):
        rid = f"recordtype/MeterReading__c.{rt}"
        assert rid in nbyid and nbyid[rid]["type"] == "recordtype"
        # contains edge object -> recordtype (asserted from extract() output:
        # the recordtype kind has no default resolver)
        assert any(e["src"] == "object/MeterReading__c" and e["type"] == "contains"
                   and e["to_kind"] == "recordtype"
                   and e["to_name"] == f"MeterReading__c.{rt}"
                   for e in edges)


def test_record_type_picklist_values_not_ingested(tmp_path):
    # CONFIDENTIALITY: record-type picklist <values> data must never appear as a
    # node, an edge target, or any attr.
    meta = _depconfig_object(tmp_path)
    nodes, edges = ObjectExtractor().extract(meta)
    blob = repr(nodes) + repr(edges)
    assert "SENSITIVE_VALUE" not in blob


def test_dependent_picklist_references_controlling_field(tmp_path):
    meta = _depconfig_object(tmp_path)
    _, edges = ObjectExtractor().extract(meta)
    # SubCategory__c -> references -> field MeterReading__c.Category__c
    assert any(e["src"] == "field/MeterReading__c.SubCategory__c"
               and e["type"] == "references" and e["to_kind"] == "field"
               and e["to_name"] == "MeterReading__c.Category__c"
               for e in edges)
    # the controlling field itself emits no self-reference
    assert not any(e["src"] == "field/MeterReading__c.Category__c"
                   and e["type"] == "references" for e in edges)


def test_lookup_filter_field_references(tmp_path):
    meta = _depconfig_object(tmp_path)
    _, edges = ObjectExtractor().extract(meta)
    refs = {e["to_name"] for e in edges
            if e["type"] == "references" and e["src"] == "field/MeterReading__c.Inspector__c"}
    # <field> on the target object, and $Source.Region__c -> current object
    assert "GlobexInspector__c.Region__c" in refs
    assert "MeterReading__c.Region__c" in refs           # $Source -> current object
    assert "GlobexInspector__c.IsActive__c" in refs      # second item's <field>
    # the literal <value> must NEVER be read
    blob = repr(edges)
    assert "SECRET-DO-NOT-INGEST" not in blob
    assert all(e["to_kind"] == "field" for e in edges
               if e["type"] == "references" and e["src"] == "field/MeterReading__c.Inspector__c")


def test_add_layer_resolves_in_graph(tmp_path):
    _depconfig_object(tmp_path)
    g = (core.GraphBuilder()
         .register(ObjectExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    assert g["errors"] == []
    ids = {n["id"]: n for n in g["nodes"]}

    # references edges (target kind `field`) resolve cleanly
    assert any(e["type"] == "references"
               and e["src"] == "field/MeterReading__c.SubCategory__c"
               and e["dst"] == "field/MeterReading__c.Category__c" for e in g["edges"])
    # $Source filter field resolved onto the current object's field
    assert any(e["type"] == "references"
               and e["src"] == "field/MeterReading__c.Inspector__c"
               and e["dst"] == "field/MeterReading__c.Region__c" for e in g["edges"])

    # recordtype nodes exist and `recordtype` is a default stub kind -> the
    # object -> recordtype `contains` edge resolves to the real recordtype node.
    assert "recordtype/MeterReading__c.Residential" in ids
    assert any(e["type"] == "contains"
               and e["dst"] == "recordtype/MeterReading__c.Residential" for e in g["edges"])
    assert [u for u in g["unresolved"] if u["to_kind"] == "recordtype"] == []

    # CONFIDENTIALITY: no record value leaked into the built graph
    blob = repr(g)
    assert "SENSITIVE_VALUE" not in blob
    assert "SECRET-DO-NOT-INGEST" not in blob


def test_broken_recordtype_and_filter_do_not_raise(tmp_path):
    obj_dir = tmp_path / "objects" / "Broken2__c"
    _w(obj_dir / "Broken2__c.object-meta.xml",
       '<?xml version="1.0" encoding="UTF-8"?>\n'
       '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
       '  <label>Broken Two</label>\n'
       '</CustomObject>\n')
    _w(obj_dir / "recordTypes" / "Bad.recordType-meta.xml", "<<<not xml")
    _w(obj_dir / "fields" / "Junk__c.field-meta.xml", "<<<also not xml")
    # must not raise; a broken record-type file still yields a node via the stem
    nodes, edges = ObjectExtractor().extract(obj_dir / "Broken2__c.object-meta.xml")
    nbyid = {n["id"]: n for n in nodes}
    assert "object/Broken2__c" in nbyid
    assert "recordtype/Broken2__c.Bad" in nbyid
    assert any(e["src"] == "object/Broken2__c" and e["type"] == "contains"
               and e["to_kind"] == "recordtype" and e["to_name"] == "Broken2__c.Bad"
               for e in edges)
