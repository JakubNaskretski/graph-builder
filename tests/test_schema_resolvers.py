"""Schema-aware resolvers (NOISE-1/2): a `__c` FIELD token misread as an object
and platform qualified calls (`String.valueOf`) are recognized against the
registry at resolve time and dropped — while declared targets keep resolving
and genuinely unknown ones keep stubbing, exactly as before.
"""
from graphbuilder import build_graph
from graphbuilder.resolvers import ApexMethodResolver, ObjectResolver


# --------------------------------------------------------------------------- #
# unit level — hand registries
# --------------------------------------------------------------------------- #
def test_object_resolver_drops_field_tokens():
    r = ObjectResolver()
    reg = {
        "object/Acme__c": {"id": "object/Acme__c", "type": "object"},
        "field/Acme__c.Total__c": {"id": "field/Acme__c.Total__c", "type": "field"},
    }
    assert r.resolve("Acme__c", reg) == "object/Acme__c"      # real object
    assert r.resolve("Total__c", reg) is False                # field token -> drop
    assert r.resolve("Other__c", reg) == "object/Other__c"    # unknown -> stub
    assert reg["object/Other__c"]["external"] is True
    assert r.resolve("Account", reg) == "object/Account"      # standard -> stub


def test_object_resolver_ignores_external_field_stubs():
    r = ObjectResolver()
    reg = {"field/X.Total__c": {"id": "field/X.Total__c", "type": "field",
                                "external": True}}
    # only DECLARED fields prove a name is a field — a stub guess proves nothing
    assert r.resolve("Total__c", reg) == "object/Total__c"


def test_apexmethod_resolver_drops_platform_calls():
    r = ApexMethodResolver()
    reg = {
        "apexmethod/AcmeHelper.run": {"id": "apexmethod/AcmeHelper.run",
                                      "type": "apexmethod"},
    }
    assert r.resolve("AcmeHelper.run", reg) == "apexmethod/AcmeHelper.run"
    assert r.resolve("String.valueOf", reg) is False          # platform -> drop
    assert r.resolve("JSON.serialize", reg) is False
    assert r.resolve("Database.executeBatch", reg) is False
    assert r.resolve("OtherSvc.go", reg) == "apexmethod/OtherSvc.go"  # stub
    assert reg["apexmethod/OtherSvc.go"]["external"] is True


def test_apexmethod_resolver_shadowing_wins():
    """An org class genuinely named like a platform type keeps its edges."""
    r = ApexMethodResolver()
    reg = {"apexclass/Crypto": {"id": "apexclass/Crypto", "type": "apexclass"}}
    # declared class Crypto -> its calls are real (stubbed at method level)
    assert r.resolve("Crypto.customHash", reg) == "apexmethod/Crypto.customHash"


# --------------------------------------------------------------------------- #
# integration — a build over a mini org
# --------------------------------------------------------------------------- #
OBJ_META = ('<?xml version="1.0"?>'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">'
            "<label>Acme</label></CustomObject>")
FLD_META = ('<?xml version="1.0"?>'
            '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">'
            "<fullName>Total__c</fullName><type>Number</type>"
            "<label>Total</label></CustomField>")
SVC = """public class AcmeSvc {
    public void run(Acme__c rec) {
        Decimal t = rec.Total__c;
        System.debug(String.valueOf(t));
        String payload = JSON.serialize(rec);
        AcmeHelper.persist(rec);
        update rec;
    }
}
"""
HELPER = """public class AcmeHelper {
    public static void persist(Acme__c rec) { upsert rec; }
}
"""


def test_build_suppresses_noise_keeps_structure(tmp_path):
    fa = tmp_path / "force-app" / "main" / "default"
    od = fa / "objects" / "Acme__c"
    (od / "fields").mkdir(parents=True)
    (od / "Acme__c.object-meta.xml").write_text(OBJ_META, "utf-8")
    (od / "fields" / "Total__c.field-meta.xml").write_text(FLD_META, "utf-8")
    (fa / "classes").mkdir()
    (fa / "classes" / "AcmeSvc.cls").write_text(SVC, "utf-8")
    (fa / "classes" / "AcmeHelper.cls").write_text(HELPER, "utf-8")

    g = build_graph(tmp_path)
    ids = {n["id"] for n in g["nodes"]}
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}

    # platform-call noise is GONE (previously external stubs)
    assert not any("String." in i or "JSON." in i or "System." in i for i in ids)
    # the field token no longer fabricates an object
    assert "object/Total__c" not in ids
    # ...while the real structure is intact
    assert "object/Acme__c" in ids and "field/Acme__c.Total__c" in ids
    assert ("apexclass/AcmeSvc", "calls", "apexmethod/AcmeHelper.persist") in edges
    assert g["unresolved"] == []   # drops are silent, not unresolved noise
