"""Tests for the approval-process extractor.

Alongside the structural edges, these check that no formula text leaks into a
node's attributes.
"""
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.approvalprocesses import ApprovalProcessExtractor

EX = ApprovalProcessExtractor()


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


# An approval process with entry criteria and two steps. Each formula references
# custom fields on the process's own object, mixed with standard tokens/functions
# (ISPICKVAL, AND, TODAY) and standard fields (Owner.Name) that must not become
# field reads.
METER_AP = """<?xml version="1.0" encoding="UTF-8"?>
<ApprovalProcess xmlns="http://soap.sforce.com/2006/04/metadata">
    <active>true</active>
    <entryCriteria>
        <formula>AND(ISPICKVAL(Status__c, 'Submitted'), Amount__c &gt; 1000)</formula>
    </entryCriteria>
    <approvalStep>
        <name>Manager Review</name>
        <entryCriteria>
            <formula>Region__c = 'North' &amp;&amp; Owner.Name != ''</formula>
        </entryCriteria>
    </approvalStep>
    <approvalStep>
        <name>Finance Review</name>
        <entryCriteria>
            <formula>NOT(ISBLANK(Approval_Notes__c)) || TODAY() > CreatedDate</formula>
        </entryCriteria>
    </approvalStep>
</ApprovalProcess>
"""


def _meter_fixture(tmp: Path) -> Path:
    ap = tmp / "approvalProcesses" / "MeterPoint__c.Submit_For_Review.approvalProcess-meta.xml"
    return _w(ap, METER_AP)


def test_handles():
    assert EX.handles(Path("x/MeterPoint__c.Submit_For_Review.approvalProcess-meta.xml")) is True
    assert EX.handles(Path("x/MeterPoint__c.approvalProcess-meta.xml")) is True  # suffix match
    assert EX.handles(Path("x/MeterPoint__c.object-meta.xml")) is False
    assert EX.handles(Path("x/Foo.cls")) is False


def test_node_and_on_edge(tmp_path):
    f = _meter_fixture(tmp_path)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)

    assert "approvalprocess/MeterPoint__c.Submit_For_Review" in ids
    n = ids["approvalprocess/MeterPoint__c.Submit_For_Review"]
    assert n["type"] == "approvalprocess"
    assert n["label"] == "MeterPoint__c.Submit_For_Review"

    assert ("approvalprocess/MeterPoint__c.Submit_For_Review", "on",
            "object", "MeterPoint__c") in _et(edges)


def test_reads_field_edges_from_criteria(tmp_path):
    """Bare `Field__c` tokens across the entry and step formulas become field
    reads, named on the process's own object."""
    f = _meter_fixture(tmp_path)
    _, edges = EX.extract(f)
    et = _et(edges)
    apid = "approvalprocess/MeterPoint__c.Submit_For_Review"

    for fld in ("Status__c", "Amount__c", "Region__c", "Approval_Notes__c"):
        assert (apid, "reads", "field", f"MeterPoint__c.{fld}") in et


def test_standard_tokens_not_read_as_fields(tmp_path):
    """Functions / operators / standard fields must NOT become field reads."""
    f = _meter_fixture(tmp_path)
    _, edges = EX.extract(f)
    read_targets = {e["to_name"] for e in edges if e["type"] == "reads"}

    # function names and standard fields/tokens (no __c) are skipped entirely
    for noise in ("MeterPoint__c.ISPICKVAL", "MeterPoint__c.AND", "MeterPoint__c.TODAY",
                  "MeterPoint__c.Owner", "MeterPoint__c.Name", "MeterPoint__c.CreatedDate",
                  "MeterPoint__c.ISBLANK", "MeterPoint__c.NOT"):
        assert noise not in read_targets
    # every read target is a `<Object>.<Field>__c`
    assert all(t.endswith("__c") for t in read_targets)


def test_field_edges_deduped(tmp_path):
    """A field referenced more than once yields exactly one reads edge."""
    dup = """<?xml version="1.0" encoding="UTF-8"?>
<ApprovalProcess xmlns="http://soap.sforce.com/2006/04/metadata">
    <entryCriteria><formula>Amount__c &gt; 0 || Amount__c &lt; 100</formula></entryCriteria>
    <approvalStep><entryCriteria><formula>Amount__c &gt; 50</formula></entryCriteria></approvalStep>
</ApprovalProcess>
"""
    f = _w(tmp_path / "approvalProcesses" / "Globex__c.Amount_Gate.approvalProcess-meta.xml", dup)
    _, edges = EX.extract(f)
    amount = [e for e in edges
              if e["type"] == "reads" and e["to_name"] == "Globex__c.Amount__c"]
    assert len(amount) == 1


def test_no_formula_text_leaks_into_node(tmp_path):
    """Formula text and literals never land in a node's attributes."""
    f = _meter_fixture(tmp_path)
    nodes, _ = EX.extract(f)
    blob = repr(nodes)
    for secret in ("Submitted", "ISPICKVAL", "1000", "North", "&&", "TODAY"):
        assert secret not in blob


def test_process_name_with_dots(tmp_path):
    """The process name may contain dots; the object is the FIRST segment only."""
    f = _w(tmp_path / "approvalProcesses" / "Acme__c.Tier.One.approvalProcess-meta.xml",
           '<?xml version="1.0"?><ApprovalProcess '
           'xmlns="http://soap.sforce.com/2006/04/metadata"><active>true</active>'
           '</ApprovalProcess>')
    nodes, edges = EX.extract(f)
    assert "approvalprocess/Acme__c.Tier.One" in _ids(nodes)
    assert ("approvalprocess/Acme__c.Tier.One", "on", "object", "Acme__c") in _et(edges)


def test_never_raises_on_broken_input(tmp_path):
    for i, text in enumerate(("", "not xml at all <<<", "<ApprovalProcess>",
                              '<?xml version="1.0"?><ApprovalProcess/>')):
        f = _w(tmp_path / "approvalProcesses"
               / f"Acme__c.Broken{i}.approvalProcess-meta.xml", text)
        nodes, edges = EX.extract(f)            # must not raise
        assert isinstance(nodes, list) and isinstance(edges, list)
        # the node + on edge are still emitted from the filename alone
        assert any(n["type"] == "approvalprocess" for n in nodes)
        assert any(e["type"] == "on" for e in edges)


def test_malformed_filename_skipped(tmp_path):
    """A name without the `<Object>.<Process>` shape emits nothing (skipped)."""
    f = _w(tmp_path / "approvalProcesses" / "NoDotHere.approvalProcess-meta.xml",
           '<?xml version="1.0"?><ApprovalProcess '
           'xmlns="http://soap.sforce.com/2006/04/metadata"/>')
    nodes, edges = EX.extract(f)
    assert nodes == [] and edges == []


def test_build_graph_resolves_and_stubs(tmp_path):
    """Isolated graph build: only ApprovalProcessExtractor + default resolvers.
    The object + referenced fields aren't in this tree, so they become stubs."""
    _meter_fixture(tmp_path)

    g = (GraphBuilder()
         .register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    assert g["errors"] == []
    ids = {n["id"]: n for n in g["nodes"]}

    assert "approvalprocess/MeterPoint__c.Submit_For_Review" in ids
    # the object + fields aren't in the repo -> external stubs
    assert ids["object/MeterPoint__c"].get("external") is True
    assert ids["field/MeterPoint__c.Amount__c"].get("external") is True

    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    apid = "approvalprocess/MeterPoint__c.Submit_For_Review"
    assert (apid, "on", "object/MeterPoint__c") in edges
    assert (apid, "reads", "field/MeterPoint__c.Status__c") in edges
    assert (apid, "reads", "field/MeterPoint__c.Approval_Notes__c") in edges
    # no edge is left unresolved (object + field both have default resolvers)
    assert g["unresolved"] == []
