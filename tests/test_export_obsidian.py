"""Obsidian exporter — vocabulary folder/label binding plus a smoke export.

The exporter must render every node/edge type in `model.NODE_TYPES` /
`EDGE_TYPES`, including future ones, without a per-type edit: known types use
curated wording, unknown ones a derived default.
"""
import importlib.util
from pathlib import Path

from graphbuilder.model import EDGE_TYPES, NODE_TYPES

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_obsidian.py"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("export_obsidian", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EX = _load_exporter()


def test_every_node_type_has_a_folder():
    for nt in NODE_TYPES:
        folder = EX._folder(nt)
        assert folder and isinstance(folder, str)
    # data-model types land in their curated folders
    assert EX._folder("listview") == "Objects"
    assert EX._folder("globalvalueset") == "Objects"
    assert EX._folder("custommetadatarecord") == "Objects"
    assert EX._folder("platformeventchannel") == "Automation"


def test_every_edge_type_has_in_and_out_labels():
    for et in EDGE_TYPES:
        assert EX._edge_out(et)
        assert EX._edge_in(et)
    # coverage-layer edges have curated wording
    assert EX._edge_out("tests") == "Tests"
    assert EX._edge_in("requires") == "Required by"


def test_unknown_type_falls_back_to_derived_default():
    """A vocabulary entry with no curated override still renders, never crashes."""
    assert EX._folder("somefuturekind") == "Somefuturekind"
    assert EX._edge_out("some-future-edge") == "Some Future Edge"
    assert EX._edge_in("some-future-edge") == "Some Future Edge (in)"


def test_smoke_export_writes_vault(tmp_path):
    # minimal force-app: one custom object with one field
    obj_dir = tmp_path / "force-app" / "objects" / "MeterPoint__c"
    obj_dir.mkdir(parents=True)
    (obj_dir / "MeterPoint__c.object-meta.xml").write_text(
        '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">'
        "<label>Meter Point</label></CustomObject>", "utf-8")
    fld_dir = obj_dir / "fields"
    fld_dir.mkdir()
    (fld_dir / "Reading__c.field-meta.xml").write_text(
        '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">'
        "<fullName>Reading__c</fullName><type>Number</type></CustomField>", "utf-8")

    vault = tmp_path / "vault"
    counts, total = EX.export(tmp_path / "force-app", vault)

    assert total >= 1
    assert (vault / "_Org Map.md").exists()
    assert (vault / "Objects" / "MeterPoint__c.md").exists()
    assert counts.get("object") == 1
