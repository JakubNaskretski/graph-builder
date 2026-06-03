"""Single-file digest (`build_file`) and multi-seed `neighborhood`."""
from graphbuilder import build_file, neighborhood

_SERVICE = (
    "public class AcmeService {\n"
    "  public void run() {\n"
    "    AcmeHelper.ping();\n"
    "    List<MeterPoint__c> m = [SELECT Id FROM MeterPoint__c];\n"
    "  }\n"
    "}\n"
)
_HELPER = "public class AcmeHelper {\n  public static void ping() {}\n}\n"


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


def _ids(g):
    return {n["id"] for n in g["nodes"]}


# --------------------------------------------------------------------------- #
# single-file digest
# --------------------------------------------------------------------------- #
def test_digest_single_file_shape(tmp_path):
    p = _w(tmp_path / "AcmeService.cls", _SERVICE)
    g = build_file(p)
    assert set(g) == {"nodes", "edges", "unresolved", "errors"}
    ids = _ids(g)
    assert "apexclass/AcmeService" in ids
    assert "apexmethod/AcmeService.run" in ids
    # off-file targets are external stubs
    assert any(n["id"] == "object/MeterPoint__c" and n.get("external") for n in g["nodes"])
    assert g["errors"] == []


def test_level_one_keeps_only_the_file(tmp_path):
    p = _w(tmp_path / "AcmeService.cls", _SERVICE)
    g = build_file(p, levels=1)
    ids = _ids(g)
    # the file's own definition (class + methods) survives...
    assert "apexclass/AcmeService" in ids
    assert "apexmethod/AcmeService.run" in ids
    # ...but nothing it references (those are level 2+)
    assert not any(n.get("external") for n in g["nodes"])
    assert "object/MeterPoint__c" not in ids


def test_level_two_adds_one_hop(tmp_path):
    p = _w(tmp_path / "AcmeService.cls", _SERVICE)
    g = build_file(p, levels=2)
    ids = _ids(g)
    assert "object/MeterPoint__c" in ids               # one hop out = level 2
    assert "apexmethod/AcmeHelper.ping" in ids          # qualified call target


def test_types_allowlist_filters_nodes(tmp_path):
    p = _w(tmp_path / "AcmeService.cls", _SERVICE)
    # level 2 reaches objects + methods; restrict to methods only
    g = build_file(p, levels=2, types="apexmethod")
    kinds = {n["type"] for n in g["nodes"]}
    assert kinds == {"apexmethod"}
    assert "object/MeterPoint__c" not in _ids(g)
    # an iterable of types is also accepted
    g2 = build_file(p, levels=2, types=["apexclass", "object"])
    assert {n["type"] for n in g2["nodes"]} <= {"apexclass", "object"}


def test_unhandled_file_is_empty_not_raised(tmp_path):
    p = _w(tmp_path / "notes.txt", "just text, no extractor")
    g = build_file(p)
    assert g == {"nodes": [], "edges": [], "unresolved": [], "errors": []}


# --------------------------------------------------------------------------- #
# repo context: edges resolve to REAL nodes, not stubs
# --------------------------------------------------------------------------- #
def test_repo_context_resolves_real_nodes(tmp_path):
    classes = tmp_path / "force-app" / "classes"
    _w(classes / "AcmeService.cls", _SERVICE)
    _w(classes / "AcmeHelper.cls", _HELPER)

    # self-contained: AcmeHelper.ping is an external stub
    g0 = build_file(classes / "AcmeService.cls", levels=2)
    ping0 = next(n for n in g0["nodes"] if n["id"] == "apexmethod/AcmeHelper.ping")
    assert ping0.get("external") is True

    # with repo context: AcmeHelper.ping is the real in-repo node
    g1 = build_file(classes / "AcmeService.cls", levels=2, repo=tmp_path / "force-app")
    ping1 = next(n for n in g1["nodes"] if n["id"] == "apexmethod/AcmeHelper.ping")
    assert ping1.get("external") is not True
    assert "apexclass/AcmeService" in _ids(g1)


def test_levels_reach_object_fields_at_level_three(tmp_path):
    """apex (1) -> object it references (2) -> that object's fields (3)."""
    fa = tmp_path / "force-app"
    _w(fa / "classes" / "AcmeService.cls", _SERVICE)
    obj = fa / "objects" / "MeterPoint__c"
    _w(obj / "MeterPoint__c.object-meta.xml",
       '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">'
       "<label>Meter</label></CustomObject>")
    _w(obj / "fields" / "Reading__c.field-meta.xml",
       '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">'
       "<fullName>Reading__c</fullName><type>Number</type></CustomField>")

    src = fa / "classes" / "AcmeService.cls"
    # level 2: object present, but its own field is one level further out
    assert "field/MeterPoint__c.Reading__c" not in _ids(build_file(src, levels=2, repo=fa))
    # level 3: the object's field is now mapped in
    g3 = build_file(src, levels=3, repo=fa)
    assert "object/MeterPoint__c" in _ids(g3)
    assert "field/MeterPoint__c.Reading__c" in _ids(g3)


# --------------------------------------------------------------------------- #
# multi-seed neighborhood
# --------------------------------------------------------------------------- #
def test_neighborhood_multi_seed():
    g = {
        "nodes": [{"id": x, "type": "t"} for x in ("a", "b", "c", "d", "e")],
        "edges": [
            {"src": "a", "dst": "b", "type": "calls"},
            {"src": "b", "dst": "c", "type": "calls"},
            {"src": "d", "dst": "e", "type": "calls"},
        ],
    }
    # seeded from a and d, 1 hop: reaches b (from a) and e (from d), not c
    sub = neighborhood(g, ["a", "d"], max_depth=1)
    assert {n["id"] for n in sub["nodes"]} == {"a", "b", "d", "e"}
    # depth 0 keeps only the seeds
    assert {n["id"] for n in neighborhood(g, ["a", "d"], max_depth=0)["nodes"]} == {"a", "d"}
