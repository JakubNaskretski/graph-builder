"""Reports & Dashboards extractor tests.

Confidentiality assertions check that no filter value, chart datum, or title text
leaks into nodes or edges — only structural object/field/report names.
"""
from pathlib import Path

from graphbuilder import core, resolvers
from graphbuilder.extractors.reports import EXTRACTORS, ReportExtractor

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'

# A value that must never appear anywhere in extractor output.
SECRET = "ConfidentialFilterValue_DoNotLeak"


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _report_xml(report_type, columns, groupings_down=(), groupings_across=()):
    cols = "".join(f"  <columns><field>{c}</field></columns>\n" for c in columns)
    gd = "".join(
        f"  <groupingsDown><field>{g}</field><sortOrder>Asc</sortOrder></groupingsDown>\n"
        for g in groupings_down
    )
    ga = "".join(
        f"  <groupingsAcross><field>{g}</field></groupingsAcross>\n"
        for g in groupings_across
    )
    # <name> (title), <filter> criteria with values, and <chart> data are all
    # present in real reports and must be ignored by the extractor.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Report {NS}>
  <name>Acme Quarterly {SECRET} Title</name>
  <reportType>{report_type}</reportType>
  <format>Summary</format>
{cols}{gd}{ga}  <filter>
    <criteriaItems>
      <column>Account.Industry</column>
      <operator>equals</operator>
      <value>{SECRET}</value>
    </criteriaItems>
  </filter>
  <chart>
    <chartType>Bar</chartType>
    <title>{SECRET}</title>
  </chart>
</Report>
"""


def _dashboard_xml(reports, with_filter_report=None):
    comps = "".join(
        f"""  <dashboardComponents>
    <componentType>Bar</componentType>
    <report>{r}</report>
  </dashboardComponents>
"""
        for r in reports
    )
    filt = ""
    if with_filter_report is not None:
        filt = f"""  <dashboardFilters>
    <name>Region</name>
    <dashboardFilterOptions>
      <operator>equals</operator>
      <value>{SECRET}</value>
    </dashboardFilterOptions>
    <report>{with_filter_report}</report>
  </dashboardFilters>
"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Dashboard {NS}>
  <title>Acme Exec {SECRET} Dashboard</title>
{comps}{filt}</Dashboard>
"""


# --------------------------------------------------------------------------- #
# registry / handles
# --------------------------------------------------------------------------- #
def test_registry_exposes_instance():
    assert len(EXTRACTORS) == 1
    assert isinstance(EXTRACTORS[0], ReportExtractor)
    assert EXTRACTORS[0].source == "salesforce"


def test_handles_only_reports_and_dashboards():
    ex = ReportExtractor()
    assert ex.handles(Path("AcmePipeline.report-meta.xml"))
    assert ex.handles(Path("AcmeExec.dashboard-meta.xml"))
    assert not ex.handles(Path("AcmeService.cls"))
    assert not ex.handles(Path("AcmeMeterPoint.flexipage-meta.xml"))
    assert not ex.handles(Path("AcmeTrigger.trigger"))


# --------------------------------------------------------------------------- #
# reports
# --------------------------------------------------------------------------- #
def test_report_node_and_base_object_on_edge(tmp_path):
    p = tmp_path / "AcmePipeline.report-meta.xml"
    _w(p, _report_xml("Opportunity", ["Opportunity.Name", "Opportunity.Amount"]))
    nodes, edges = ReportExtractor().extract(p)

    assert nodes == [{"id": "report/AcmePipeline", "type": "report",
                      "label": "AcmePipeline"}]
    # <reportType> base object -> on edge
    assert {"src": "report/AcmePipeline", "type": "on",
            "to_kind": "object", "to_name": "Opportunity"} in edges


def test_report_columns_and_groupings_read_fields(tmp_path):
    p = tmp_path / "AcmeUsage.report-meta.xml"
    _w(p, _report_xml(
        "MeterPoint__c",
        columns=["MeterPoint__c.Name", "MeterPoint__c.Consumption__c"],
        groupings_down=["MeterPoint__c.Region__c"],
        groupings_across=["Account.Name"],
    ))
    nodes, edges = ReportExtractor().extract(p)

    reads = {(e["to_kind"], e["to_name"]) for e in edges if e["type"] == "reads"}
    assert ("field", "MeterPoint__c.Name") in reads
    assert ("field", "MeterPoint__c.Consumption__c") in reads
    assert ("field", "MeterPoint__c.Region__c") in reads      # groupingsDown
    assert ("field", "Account.Name") in reads                  # groupingsAcross


def test_object_less_field_tokens_are_skipped_not_guessed(tmp_path):
    """Bare standard column, bucket field, and summary formula have no determinable
    object, so they are skipped rather than guessed."""
    p = tmp_path / "AcmeStd.report-meta.xml"
    _w(p, _report_xml(
        "Opportunity",
        columns=["OPPORTUNITY_TYPE", "BucketField_12345", "FORMULA1",
                 "Opportunity.StageName"],
    ))
    nodes, edges = ReportExtractor().extract(p)

    reads = {e["to_name"] for e in edges if e["type"] == "reads"}
    assert reads == {"Opportunity.StageName"}                  # only the dotted one
    # nothing object-less got through
    assert "OPPORTUNITY_TYPE" not in reads
    assert not any("BucketField" in r or "FORMULA" in r for r in reads)


def test_relationship_path_collapses_to_root_object_and_final_field(tmp_path):
    p = tmp_path / "AcmeRel.report-meta.xml"
    _w(p, _report_xml("Opportunity", ["Account.Owner.Email"]))
    _, edges = ReportExtractor().extract(p)
    reads = {e["to_name"] for e in edges if e["type"] == "reads"}
    assert reads == {"Account.Email"}


def test_tabular_reporttype_with_no_object_skips_on_edge(tmp_path):
    p = tmp_path / "AcmeTab.report-meta.xml"
    _w(p, _report_xml("Tabular", ["Account.Name"]))
    _, edges = ReportExtractor().extract(p)
    assert all(e["type"] != "on" for e in edges)
    # field reads still emitted where determinable
    assert any(e["type"] == "reads" and e["to_name"] == "Account.Name" for e in edges)


def test_report_emits_no_filter_or_chart_values(tmp_path):
    """Confidentiality: the filter value, chart title, and report title must not
    appear in any node or edge."""
    p = tmp_path / "AcmeConf.report-meta.xml"
    _w(p, _report_xml("Opportunity", ["Opportunity.Amount"]))
    nodes, edges = ReportExtractor().extract(p)

    blob = repr(nodes) + repr(edges)
    assert SECRET not in blob
    # node label is the api name, never the <name> title
    assert nodes[0]["label"] == "AcmeConf"
    # the filter's column (Account.Industry) is NOT a column/grouping -> not read
    assert all(e["to_name"] != "Account.Industry" for e in edges if e["type"] == "reads")


def test_report_no_columns_only_node(tmp_path):
    p = tmp_path / "AcmeEmpty.report-meta.xml"
    _w(p, _report_xml("Opportunity", []))
    nodes, edges = ReportExtractor().extract(p)
    assert nodes[0]["id"] == "report/AcmeEmpty"
    # only the base-object `on` edge, no reads
    assert all(e["type"] != "reads" for e in edges)


# --------------------------------------------------------------------------- #
# dashboards
# --------------------------------------------------------------------------- #
def test_dashboard_uses_reports(tmp_path):
    p = tmp_path / "AcmeExec.dashboard-meta.xml"
    _w(p, _dashboard_xml(
        ["AcmeFolder/AcmePipeline", "AcmeUsage"],
        with_filter_report="AcmeFolder/AcmeRevenue",
    ))
    nodes, edges = ReportExtractor().extract(p)

    assert nodes == [{"id": "dashboard/AcmeExec", "type": "dashboard",
                      "label": "AcmeExec"}]
    uses = {(e["type"], e["to_kind"], e["to_name"]) for e in edges}
    # component report paths -> trailing segment is the report name
    assert ("uses", "report", "AcmePipeline") in uses
    assert ("uses", "report", "AcmeUsage") in uses
    # dashboardFilters report reference also captured
    assert ("uses", "report", "AcmeRevenue") in uses


def test_dashboard_emits_no_filter_values(tmp_path):
    p = tmp_path / "AcmeConfDash.dashboard-meta.xml"
    _w(p, _dashboard_xml(["AcmeFolder/AcmePipeline"],
                         with_filter_report="AcmeFolder/AcmePipeline"))
    nodes, edges = ReportExtractor().extract(p)
    blob = repr(nodes) + repr(edges)
    assert SECRET not in blob
    assert nodes[0]["label"] == "AcmeConfDash"


def test_dashboard_dedupes_report_refs(tmp_path):
    p = tmp_path / "AcmeDup.dashboard-meta.xml"
    _w(p, _dashboard_xml(["AcmeFolder/AcmePipeline", "OtherFolder/AcmePipeline"]))
    _, edges = ReportExtractor().extract(p)
    uses = [e for e in edges if e["type"] == "uses" and e["to_name"] == "AcmePipeline"]
    assert len(uses) == 1


# --------------------------------------------------------------------------- #
# robustness
# --------------------------------------------------------------------------- #
def test_broken_report_xml_is_skipped_not_raised(tmp_path):
    p = tmp_path / "AcmeBroken.report-meta.xml"
    _w(p, f"<Report {NS}><reportType>Opportunity</reportType")  # unterminated
    nodes, edges = ReportExtractor().extract(p)
    assert nodes[0]["id"] == "report/AcmeBroken"
    assert edges == []


def test_broken_dashboard_xml_is_skipped_not_raised(tmp_path):
    p = tmp_path / "AcmeBrokenDash.dashboard-meta.xml"
    _w(p, f"<Dashboard {NS}><dashboardComponents><report>X")  # unterminated
    nodes, edges = ReportExtractor().extract(p)
    assert nodes[0]["id"] == "dashboard/AcmeBrokenDash"
    assert edges == []


# --------------------------------------------------------------------------- #
# graph build
# --------------------------------------------------------------------------- #
def test_build_graph_in_isolation(tmp_path):
    rp = tmp_path / "reports" / "AcmePipeline.report-meta.xml"
    _w(rp, _report_xml("Opportunity", ["Opportunity.Name", "Opportunity.Amount"]))
    dp = tmp_path / "dashboards" / "AcmeExec.dashboard-meta.xml"
    _w(dp, _dashboard_xml(["AcmeFolder/AcmePipeline"]))

    g = (core.GraphBuilder()
         .register(ReportExtractor())
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    ids = {n["id"]: n for n in g["nodes"]}
    assert "report/AcmePipeline" in ids
    assert "dashboard/AcmeExec" in ids
    # base object / fields resolve to external stubs
    assert ids["object/Opportunity"].get("external") is True
    assert ids["field/Opportunity.Name"].get("external") is True
    assert any(e["type"] == "on" and e["dst"] == "object/Opportunity" for e in g["edges"])
    assert any(e["type"] == "reads" and e["dst"] == "field/Opportunity.Amount"
               for e in g["edges"])
    assert g["errors"] == []

    # the dashboard's uses -> report edge is present in extract() output
    _, dash_edges = ReportExtractor().extract(dp)
    assert {"src": "dashboard/AcmeExec", "type": "uses",
            "to_kind": "report", "to_name": "AcmePipeline"} in dash_edges
    # report/AcmePipeline exists and `report` is a default stub kind, so the
    # dashboard's uses -> report edge resolves to the real report node
    assert any(e["type"] == "uses" and e["src"] == "dashboard/AcmeExec"
               and e["dst"] == "report/AcmePipeline" for e in g["edges"])
    assert g["unresolved"] == []
