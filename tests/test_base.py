"""Core framework mechanics (extract -> resolve -> external stub) and the
depth-limited, cycle-safe traversal, checked on hand-built graphs."""
from graphbuilder import build_graph, model
from graphbuilder.model import NODE_TYPES
from graphbuilder.resolvers import STUB_KINDS


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def test_stub_kinds_track_the_vocabulary():
    """STUB_KINDS derives from NODE_TYPES, so a new node type can never be left
    without a resolver. Every type gets a stub except the dedicated resolvers:
    `label` (prefix normalization), `page` (title-form -> page-id mapping), and
    the schema-aware `object` / `apexmethod` (platform-noise suppression)."""
    assert set(STUB_KINDS) == NODE_TYPES - {"label", "page", "object", "apexmethod"}
    assert not {"label", "page", "object", "apexmethod"} & set(STUB_KINDS)
    # the LWC dependency stub targets are part of the vocabulary, not just resolver-only
    assert {"resource", "messagechannel"} <= NODE_TYPES


def test_framework_extract_and_external_stub(tmp_path):
    fa = tmp_path / "force-app" / "main" / "default"
    _w(fa / "triggers" / "MeterPointTrigger.trigger",
       "trigger MeterPointTrigger on MeterPoint__c (after insert) {\n  MeterPointService.run();\n}\n")
    g = build_graph(tmp_path)
    ids = {n["id"]: n for n in g["nodes"]}
    assert "trigger/MeterPointTrigger" in ids
    # 'on' target resolved to an EXTERNAL stub object (not in the repo)
    assert "object/MeterPoint__c" in ids and ids["object/MeterPoint__c"].get("external") is True
    assert any(e["type"] == "on" and e["src"] == "trigger/MeterPointTrigger"
               and e["dst"] == "object/MeterPoint__c" for e in g["edges"])
    assert g["errors"] == [] and g["unresolved"] == []     # stub resolvers wire everything


def test_traverse_depth_limited():
    g = {"nodes": [{"id": f"apexclass/{x}", "type": "apexclass", "label": x} for x in "ABC"],
         "edges": [{"src": "apexclass/A", "dst": "apexclass/B", "type": "calls"},
                   {"src": "apexclass/B", "dst": "apexclass/C", "type": "calls"}]}
    d1 = [n for n, _, _ in model.traverse(g, "apexclass/A", "out", max_depth=1)]
    assert d1 == ["apexclass/B"]                            # stops at 1 hop
    d2 = [n for n, _, _ in model.traverse(g, "apexclass/A", "out", max_depth=2)]
    assert "apexclass/C" in d2                              # 2 hops reached


def test_cycle_is_safe_and_reported():
    g = {"nodes": [{"id": "apexclass/A", "type": "apexclass", "label": "A"},
                   {"id": "apexclass/B", "type": "apexclass", "label": "B"}],
         "edges": [{"src": "apexclass/A", "dst": "apexclass/B", "type": "calls"},
                   {"src": "apexclass/B", "dst": "apexclass/A", "type": "calls"}]}
    reached = [n for n, _, _ in model.traverse(g, "apexclass/A", "out", max_depth=None)]
    assert "apexclass/B" in reached                         # terminates despite the cycle
    assert model.find_cycles(g)                             # cycle reported
