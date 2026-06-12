"""Tests for the Apex extractor."""
from pathlib import Path

import pytest

import graphbuilder.resolvers as resolvers
import graphbuilder.extractors.apex as apexmod
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.apex import ApexExtractor

EX = ApexExtractor()

# The AST backend is optional; its precision is only asserted when the apex
# grammar is loadable in this environment.
AST_AVAILABLE = apexmod._APEX_PARSER is not None
ast_only = pytest.mark.skipif(not AST_AVAILABLE, reason="apex tree-sitter grammar not installed")


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")
    return p


# A representative class exercising every feature of the extractor.
SERVICE = """public with sharing class AcmeMeterPointService extends BaseService
        implements Database.Batchable<sObject>, Schedulable {

    @AuraEnabled
    public static List<MeterPoint__c> getActiveMeterPoints(Id accountId) {
        List<MeterPoint__c> mps = [SELECT Id, Name FROM MeterPoint__c WHERE Active__c = true];
        logAccess(accountId);
        return mps;
    }

    @InvocableMethod(label='Recalculate readings')
    public static void recalc(List<Id> ids) {
        List<Reading__c> rs = [SELECT Id FROM Reading__c WHERE Id IN :ids];
        delete new Stale__c();
    }

    @future(callout=true)
    public static void pushToBilling(Id mpId) {
        insert new BillingEvent__c(MeterPoint__c = mpId);
    }

    private void logAccess(Id who) {
        AcmeLogger.write(who);
    }
}
"""


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def test_class_and_base_edges():
    f = _w(Path("/tmp/_acme_apex/AcmeMeterPointService.cls"), SERVICE)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)

    # apexclass node
    assert "apexclass/AcmeMeterPointService" in ids
    assert ids["apexclass/AcmeMeterPointService"]["type"] == "apexclass"

    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    # references -> object (custom-object/field tokens from the base parser)
    assert ("apexclass/AcmeMeterPointService", "references", "object", "MeterPoint__c") in et
    # extends / implements -> apexclass
    assert ("apexclass/AcmeMeterPointService", "extends", "apexclass", "BaseService") in et
    assert ("apexclass/AcmeMeterPointService", "implements", "apexclass", "Batchable") in et
    assert ("apexclass/AcmeMeterPointService", "implements", "apexclass", "Schedulable") in et


def test_class_kind_reflects_implemented_async_iface_both_backends():
    """The apexclass `kind` reflects the async interface the class implements,
    identically under the AST and regex backends — including the generic
    `Database.Batchable<sObject>` form."""
    cases = [
        # generic Batchable wins over Schedulable (matches parse_apex precedence)
        ("public class KGen implements Database.Batchable<sObject>, Schedulable { void e(){} }",
         "batch"),
        ("public class KB implements Batchable { void e(){} }", "batch"),
        ("public class KS implements Schedulable { void e(){} }", "schedulable"),
        # Queueable is async but is not a `batch`/`schedulable` class kind
        ("public class KQ implements Queueable { void e(){} }", "class"),
        ("public class KPlain { void e(){} }", "class"),
    ]
    for i, (src, expected) in enumerate(cases):
        f = _w(Path(f"/tmp/_acme_apex/Kind{i}.cls"), src)
        rgx = _ids(EX._extract_regex(f)[0])
        cid = next(k for k in rgx if k.startswith("apexclass/"))
        assert rgx[cid]["kind"] == expected, f"regex: {src!r}"
        if AST_AVAILABLE:
            ast_nodes = _ids(EX._extract_ast(f)[0])
            assert ast_nodes[cid]["kind"] == expected, f"ast: {src!r}"


def test_parse_apex_captures_generic_implements():
    """parse_apex keeps a generic interface: `implements` holds the
    generic-stripped names and `kind` reflects them."""
    from graphbuilder.salesforce import parse_apex
    f = _w(Path("/tmp/_acme_apex/KGenParse.cls"),
           "public class KGenParse implements Database.Batchable<sObject>, Schedulable {\n"
           "  public void execute() {}\n}\n")
    cls = parse_apex(f)
    assert cls.implements == ["Database.Batchable", "Schedulable"]
    assert cls.kind == "batch"


def test_regex_backend_detects_modifier_less_methods_no_false_positives(monkeypatch):
    """The regex fallback catches modifier-less methods (interface methods,
    default-access class methods) without turning statements or calls into
    phantom method nodes."""
    monkeypatch.setattr(apexmod, "_APEX_PARSER", None)        # force regex backend

    # bare interface methods (no access modifier)
    f = _w(Path("/tmp/_acme_apex/AcmeCalc2.cls"),
           "public interface AcmeCalc2 {\n"
           "    Integer add(Integer a, Integer b);\n"
           "    String describe();\n"
           "}\n")
    mids = {n["id"] for n in EX.extract(f)[0] if n["type"] == "apexmethod"}
    assert "apexmethod/AcmeCalc2.add" in mids
    assert "apexmethod/AcmeCalc2.describe" in mids

    # default-access class method + a body full of statements that must NOT be
    # mistaken for declarations.
    f2 = _w(Path("/tmp/_acme_apex/AcmeWorker.cls"),
            "public class AcmeWorker {\n"
            "    void helper(Account acc) {\n"               # no access modifier
            "        Integer n = compute(acc);\n"            # local decl w/ call
            "        if (n > 0) { return; }\n"               # control keyword
            "        Account a = new Account();\n"           # new
            "        insert a;\n"
            "        System.debug(a.format());\n"            # chained call
            "        return helper2();\n"                    # return call
            "    }\n"
            "    public Integer compute(Account acc) { return 1; }\n"
            "    Integer helper2() { return 2; }\n"          # another modifier-less
            "}\n")
    got = {n["id"].split("/", 1)[1] for n in EX.extract(f2)[0]
           if n["type"] == "apexmethod"}
    real = {"AcmeWorker.helper", "AcmeWorker.compute", "AcmeWorker.helper2"}
    assert real <= got, f"missing real methods: {real - got}"
    assert not (got - real), f"phantom method nodes from statements: {got - real}"


def test_method_nodes_contains_and_annotations():
    f = _w(Path("/tmp/_acme_apex/AcmeMeterPointService.cls"), SERVICE)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)

    # one apexmethod node per method + contains edge from the class
    for m in ("getActiveMeterPoints", "recalc", "pushToBilling", "logAccess"):
        mid = f"apexmethod/AcmeMeterPointService.{m}"
        assert mid in ids and ids[mid]["type"] == "apexmethod"
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert ("apexclass/AcmeMeterPointService", "contains", "apexmethod",
            "AcmeMeterPointService.getActiveMeterPoints") in et

    # annotation flags on the method nodes
    assert "auraenabled" in ids["apexmethod/AcmeMeterPointService.getActiveMeterPoints"]["annotations"]
    assert "invocablemethod" in ids["apexmethod/AcmeMeterPointService.recalc"]["annotations"]
    assert "future" in ids["apexmethod/AcmeMeterPointService.pushToBilling"]["annotations"]
    # un-annotated method carries no annotations attr
    assert "annotations" not in ids["apexmethod/AcmeMeterPointService.logAccess"]


def test_method_calls_reads_writes_and_async():
    f = _w(Path("/tmp/_acme_apex/AcmeMeterPointService.cls"), SERVICE)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}

    # intra-class method -> method call (getActiveMeterPoints -> logAccess)
    assert ("apexmethod/AcmeMeterPointService.getActiveMeterPoints", "calls",
            "apexmethod", "AcmeMeterPointService.logAccess") in et

    # per-method reads (SOQL FROM) and writes (DML literal)
    assert ("apexmethod/AcmeMeterPointService.getActiveMeterPoints", "reads",
            "object", "MeterPoint__c") in et
    assert ("apexmethod/AcmeMeterPointService.recalc", "reads", "object", "Reading__c") in et
    assert ("apexmethod/AcmeMeterPointService.recalc", "writes", "object", "Stale__c") in et
    assert ("apexmethod/AcmeMeterPointService.pushToBilling", "writes",
            "object", "BillingEvent__c") in et

    # async: class-level (Batchable/Schedulable) + method-level (@future)
    assert "async_kind" in ids["apexclass/AcmeMeterPointService"]
    ak = set(ids["apexclass/AcmeMeterPointService"]["async_kind"])
    assert {"batchable", "schedulable", "future"} <= ak
    async_edges = {(e["src"], e["to_name"]) for e in edges if e["type"] == "async"}
    assert ("apexmethod/AcmeMeterPointService.pushToBilling", "System.Future") in async_edges
    assert ("apexclass/AcmeMeterPointService", "Database.Batchable") in async_edges


def test_overloads_collapse_to_one_node():
    f = _w(Path("/tmp/_acme_apex/AcmeCalc.cls"),
           "public class AcmeCalc {\n"
           "  @AuraEnabled public static Decimal add(Decimal a, Decimal b){ return a + b; }\n"
           "  public static Decimal add(Decimal a){ return a; }\n}\n")
    nodes, _ = EX.extract(f)
    mids = [n["id"] for n in nodes if n["type"] == "apexmethod"]
    assert mids == ["apexmethod/AcmeCalc.add"]                 # collapsed
    add = _ids(nodes)["apexmethod/AcmeCalc.add"]
    assert "auraenabled" in add["annotations"]
    # the single node records how many declarations were seen, and the FIRST
    # declaration's signature wins for the attrs
    assert add["overloads"] == 2
    assert add["return_type"] == "Decimal"
    assert add["visibility"] == "public"
    assert add["is_static"] is True
    assert add["parameters"] == [{"type": "Decimal", "name": "a"},
                                 {"type": "Decimal", "name": "b"}]


# A multi-method class exercising the signature attrs on both backends:
# visibility, static, return types (incl. nested generics), parameters (a
# generic param whose inner comma must NOT split it), a constructor, and a
# method with no stated modifiers.
SIGNATURES = """public without sharing class AcmeTariffEngine {
    public static Map<Id, List<Reading__c>> readingsByMeter(Map<Id ,List<Account>> byAcct, Integer max) {
        return null;
    }
    private List<Rate__c> activeRates(String regionCode) { return null; }
    protected virtual void applyTariff(Rate__c rate) {}
    global Boolean isLive() { return true; }
    Integer bare() { return 0; }
    public AcmeTariffEngine(String mode) {}
}
"""


def _assert_signature_attrs(ids):
    """Signature assertions shared by the regex and AST backend tests."""
    pre = "apexmethod/AcmeTariffEngine."

    m = ids[pre + "readingsByMeter"]
    assert m["visibility"] == "public"
    assert m["is_static"] is True
    assert m["return_type"] == "Map<Id, List<Reading__c>>"
    # generic param with an inner comma stays ONE parameter, whitespace normalised
    assert m["parameters"] == [
        {"type": "Map<Id, List<Account>>", "name": "byAcct"},
        {"type": "Integer", "name": "max"},
    ]

    m = ids[pre + "activeRates"]
    assert m["visibility"] == "private"
    assert "is_static" not in m                       # omitted when not static
    assert m["return_type"] == "List<Rate__c>"
    assert m["parameters"] == [{"type": "String", "name": "regionCode"}]

    m = ids[pre + "applyTariff"]
    assert m["visibility"] == "protected"
    assert m["return_type"] == "void"

    m = ids[pre + "isLive"]
    assert m["visibility"] == "global"
    assert m["return_type"] == "Boolean"
    assert "parameters" not in m                      # omitted when empty

    m = ids[pre + "bare"]
    assert "visibility" not in m                      # omitted when unstated
    assert m["return_type"] == "Integer"

    # not overloaded -> no overloads attr anywhere
    assert all("overloads" not in n for n in ids.values())
    # class-level sharing modifier
    assert ids["apexclass/AcmeTariffEngine"]["sharing"] == "without"


def test_method_signature_attrs_regex_backend():
    f = _w(Path("/tmp/_acme_apex/AcmeTariffEngine.cls"), SIGNATURES)
    ids = _ids(EX._extract_regex(f)[0])
    _assert_signature_attrs(ids)
    # constructor: kept as a method node, visibility recorded, NO return_type
    ctor = ids["apexmethod/AcmeTariffEngine.AcmeTariffEngine"]
    assert ctor["visibility"] == "public"
    assert "return_type" not in ctor
    assert ctor["parameters"] == [{"type": "String", "name": "mode"}]


@ast_only
def test_method_signature_attrs_ast_backend():
    f = _w(Path("/tmp/_acme_apex/AcmeTariffEngine.cls"), SIGNATURES)
    ids = _ids(EX._extract_ast(f)[0])
    _assert_signature_attrs(ids)
    # AST-only precision: 1-based start/end lines on every method node
    m = ids["apexmethod/AcmeTariffEngine.readingsByMeter"]
    assert (m["start_line"], m["end_line"]) == (2, 4)
    m = ids["apexmethod/AcmeTariffEngine.activeRates"]
    assert (m["start_line"], m["end_line"]) == (5, 5)


def test_class_sharing_attr_both_backends():
    cases = [
        ("public with sharing class Sh0 { void e(){} }", "with"),
        ("public without sharing class Sh1 { void e(){} }", "without"),
        ("public inherited sharing class Sh2 { void e(){} }", "inherited"),
        ("public class Sh3 { void e(){} }", None),
    ]
    for i, (src, expected) in enumerate(cases):
        f = _w(Path(f"/tmp/_acme_apex/Sh{i}.cls"), src)
        cid = f"apexclass/Sh{i}"
        rgx = _ids(EX._extract_regex(f)[0])
        if expected is None:
            assert "sharing" not in rgx[cid], f"regex: {src!r}"
        else:
            assert rgx[cid]["sharing"] == expected, f"regex: {src!r}"
        if AST_AVAILABLE:
            astn = _ids(EX._extract_ast(f)[0])
            if expected is None:
                assert "sharing" not in astn[cid], f"ast: {src!r}"
            else:
                assert astn[cid]["sharing"] == expected, f"ast: {src!r}"


def test_inner_class_sharing_never_leaks_to_top_class():
    src = ("public class AcmeOuter {\n"
           "  public without sharing class Inner { void e(){} }\n"
           "}\n")
    f = _w(Path("/tmp/_acme_apex/AcmeOuter.cls"), src)
    rgx = _ids(EX._extract_regex(f)[0])
    assert "sharing" not in rgx["apexclass/AcmeOuter"]
    if AST_AVAILABLE:
        astn = _ids(EX._extract_ast(f)[0])
        assert "sharing" not in astn["apexclass/AcmeOuter"]


def test_interface_method_signature_attrs_both_backends():
    """Interface methods (semicolon bodies, no access modifiers) are still
    emitted and carry return_type/parameters; visibility stays omitted."""
    src = ("public interface AcmeRater {\n"
           "    Decimal rate(MeterPoint__c mp, Map<Id, List<Account>> ctx);\n"
           "}\n")
    f = _w(Path("/tmp/_acme_apex/AcmeRater.cls"), src)
    expected_params = [
        {"type": "MeterPoint__c", "name": "mp"},
        {"type": "Map<Id, List<Account>>", "name": "ctx"},
    ]
    for backend in [EX._extract_regex] + ([EX._extract_ast] if AST_AVAILABLE else []):
        ids = _ids(backend(f)[0])
        m = ids["apexmethod/AcmeRater.rate"]
        assert m["return_type"] == "Decimal"
        assert m["parameters"] == expected_params
        assert "visibility" not in m and "is_static" not in m


def test_handles():
    assert EX.handles(Path("x/AcmeFoo.cls")) is True
    assert EX.handles(Path("x/AcmeFoo.cls-meta.xml")) is False
    assert EX.handles(Path("x/AcmeFoo.trigger")) is False


def test_never_raises_on_broken_source():
    for text in ("", "}}}{{{ not apex", "public class Broken { void x( {"):
        f = _w(Path("/tmp/_acme_apex/Broken.cls"), text)
        nodes, edges = EX.extract(f)          # must not raise
        assert isinstance(nodes, list) and isinstance(edges, list)
        assert any(n["type"] == "apexclass" for n in nodes)


# A second fixture exercising every edge kind the extractor emits.
FIDELITY = """public with sharing class AcmeBillingEngine {

    public static void process(Id acctId) {
        List<MeterPoint__c> mps =
            [SELECT Id, Name, Active__c FROM MeterPoint__c WHERE Active__c = true];
        Reading__c r = new Reading__c();
        update r;
        Database.insert(mps);
        Rate__mdt cfg = Rate__mdt.getInstance('Standard');
        Map<String, Rate__mdt> all = Rate__mdt.getAll();
        BillingSetting__c def = BillingSetting__c.getOrgDefaults();
        BillingSetting__c one = BillingSetting__c.getInstance('Acme');
        List<Invoice__c> inv = Database.getQueryLocator('SELECT Id FROM Invoice__c');
        Integer n = Database.countQuery('SELECT count() FROM Charge__c');
        System.enqueueJob(new AcmeRecalcQueueable());
        Database.executeBatch(new AcmeNightlyBatch(), 200);
        System.schedule('Nightly', '0 0 0 * * ?', new AcmeNightlyScheduler());
        GlobexLogger.write(acctId);
        AcmeFormatter.format(r);
    }
}
"""


def _et(edges):
    return {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


def test_method_level_qualified_calls():
    f = _w(Path("/tmp/_acme_apex/AcmeBillingEngine.cls"), FIDELITY)
    _, edges = EX.extract(f)
    et = _et(edges)
    cid = "apexclass/AcmeBillingEngine"
    # ClassName.method( -> calls -> apexmethod/ClassName.method
    assert (cid, "calls", "apexmethod", "GlobexLogger.write") in et
    assert (cid, "calls", "apexmethod", "AcmeFormatter.format") in et
    # framework-qualified calls are still legitimate calls
    assert (cid, "calls", "apexmethod", "Database.insert") in et
    assert (cid, "calls", "apexmethod", "System.enqueueJob") in et


def test_soql_field_selection_reads():
    f = _w(Path("/tmp/_acme_apex/AcmeBillingEngine.cls"), FIDELITY)
    _, edges = EX.extract(f)
    et = _et(edges)
    mid = "apexmethod/AcmeBillingEngine.process"
    # [SELECT Id, Name, Active__c FROM MeterPoint__c] -> reads -> field (Obj.field)
    assert (mid, "reads", "field", "MeterPoint__c.Id") in et
    assert (mid, "reads", "field", "MeterPoint__c.Name") in et
    assert (mid, "reads", "field", "MeterPoint__c.Active__c") in et
    # the object-level read is still present alongside the field reads
    assert (mid, "reads", "object", "MeterPoint__c") in et


def test_dynamic_soql_object_reads():
    f = _w(Path("/tmp/_acme_apex/AcmeBillingEngine.cls"), FIDELITY)
    _, edges = EX.extract(f)
    et = _et(edges)
    mid = "apexmethod/AcmeBillingEngine.process"
    # Database.getQueryLocator / countQuery string-literal FROM -> reads -> object
    assert (mid, "reads", "object", "Invoice__c") in et
    assert (mid, "reads", "object", "Charge__c") in et


def test_dynamic_soql_skips_variable_arg():
    """A Database.query(variable) call can't be resolved from the call site; we
    must NOT mis-associate an unrelated nearby literal to it."""
    src = ("public class AcmeQ {\n"
           "  public static void run() {\n"
           "    String soql = 'SELECT Id FROM Hidden__c';\n"
           "    List<SObject> a = Database.query(soql);\n"
           "    List<SObject> b = Database.getQueryLocator('SELECT Id FROM Visible__c');\n"
           "  }\n}\n")
    f = _w(Path("/tmp/_acme_apex/AcmeQ.cls"), src)
    _, edges = EX.extract(f)
    et = _et(edges)
    mid = "apexmethod/AcmeQ.run"
    # the literal-arg locator resolves to its object
    assert (mid, "reads", "object", "Visible__c") in et


def test_precise_dml_writes():
    f = _w(Path("/tmp/_acme_apex/AcmeBillingEngine.cls"), FIDELITY)
    _, edges = EX.extract(f)
    et = _et(edges)
    mid = "apexmethod/AcmeBillingEngine.process"
    # bare `update r;` resolves via the typed local `Reading__c r`
    assert (mid, "writes", "object", "Reading__c") in et
    # `Database.insert(mps)` resolves via the typed local `List<MeterPoint__c> mps`
    assert (mid, "writes", "object", "MeterPoint__c") in et


def test_callsite_async_edges_and_kinds():
    f = _w(Path("/tmp/_acme_apex/AcmeBillingEngine.cls"), FIDELITY)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)
    mid = "apexmethod/AcmeBillingEngine.process"
    async_edges = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges
                   if e["type"] == "async"}
    # enqueueJob (queueable), executeBatch (batchable), schedule (schedulable),
    # each targeting the determinable `new Foo()` class
    assert (mid, "async", "apexclass", "AcmeRecalcQueueable") in async_edges
    assert (mid, "async", "apexclass", "AcmeNightlyBatch") in async_edges
    assert (mid, "async", "apexclass", "AcmeNightlyScheduler") in async_edges
    # the class node records the async_kind set (this class implements nothing
    # async itself — the kinds come purely from the call sites)
    ak = set(ids["apexclass/AcmeBillingEngine"].get("async_kind", []))
    assert {"queueable", "batchable", "schedulable"} <= ak


def test_async_callsite_falls_back_to_framework_when_indeterminate():
    src = ("public class AcmeAsync {\n"
           "  public static void go(Queueable job) {\n"
           "    System.enqueueJob(job);\n"   # not a `new Foo()` -> framework target
           "  }\n}\n")
    f = _w(Path("/tmp/_acme_apex/AcmeAsync.cls"), src)
    _, edges = EX.extract(f)
    async_edges = {(e["src"], e["to_name"]) for e in edges if e["type"] == "async"}
    assert ("apexmethod/AcmeAsync.go", "Queueable") in async_edges


def test_custom_metadata_and_settings_references():
    f = _w(Path("/tmp/_acme_apex/AcmeBillingEngine.cls"), FIDELITY)
    _, edges = EX.extract(f)
    et = _et(edges)
    cid = "apexclass/AcmeBillingEngine"
    # Type__mdt.getInstance / getAll -> references -> object
    assert (cid, "references", "object", "Rate__mdt") in et
    # Settings__c.getOrgDefaults / getInstance -> references -> object
    assert (cid, "references", "object", "BillingSetting__c") in et


def test_fidelity_build_graph_resolves(tmp_path):
    """The new edges resolve through the default resolvers in an isolated build."""
    fa = tmp_path / "force-app" / "main" / "default" / "classes"
    _w(fa / "AcmeBillingEngine.cls", FIDELITY)
    g = (GraphBuilder()
         .register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    assert g["errors"] == []
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    mid = "apexmethod/AcmeBillingEngine.process"
    cid = "apexclass/AcmeBillingEngine"
    # field read resolves to a field stub
    assert (mid, "reads", "field/MeterPoint__c.Id") in edges
    # qualified method call resolves to an apexmethod stub
    assert (cid, "calls", "apexmethod/GlobexLogger.write") in edges
    # call-site async resolves to the enqueued apexclass stub
    assert (mid, "async", "apexclass/AcmeRecalcQueueable") in edges
    # custom-metadata reference resolves to an object stub
    assert (cid, "references", "object/Rate__mdt") in edges


def test_build_graph_resolves_and_stubs(tmp_path):
    """Isolated graph build: only ApexExtractor + default resolvers. Edge targets
    resolve to real nodes when present, else to external stubs."""
    fa = tmp_path / "force-app" / "main" / "default" / "classes"
    _w(fa / "AcmeMeterPointService.cls", SERVICE)
    _w(fa / "BaseService.cls", "public abstract class BaseService {\n  public void init(){}\n}\n")

    g = (GraphBuilder()
         .register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))

    assert g["errors"] == []
    ids = {n["id"]: n for n in g["nodes"]}

    # the in-repo superclass resolves to the REAL node (not a stub)
    assert "apexclass/BaseService" in ids
    assert ids["apexclass/BaseService"].get("external") is not True
    # a referenced-but-absent object becomes an external stub
    assert ids["object/MeterPoint__c"].get("external") is True

    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert ("apexclass/AcmeMeterPointService", "extends", "apexclass/BaseService") in edges
    assert ("apexclass/AcmeMeterPointService", "contains",
            "apexmethod/AcmeMeterPointService.recalc") in edges
    assert ("apexmethod/AcmeMeterPointService.getActiveMeterPoints", "calls",
            "apexmethod/AcmeMeterPointService.logAccess") in edges


# Optional tree-sitter AST backend, gated on the module-level `_APEX_PARSER`
# (None when the grammar is unavailable). The tests below assert the AST-only
# precision; the forced-regex-fallback tests at the end re-assert the core edges.

# A class with a symbol table to resolve (params + typed locals), a nested class,
# and comment/string traps that must not become edges.
AST_FIXTURE = """public with sharing class AcmeOrderService {
    // calc.ghost() and DangerComment.run() live in a comment -> NOT edges
    public void process(MeterPoint__c mp, AcmeFormatter fmt) {
        AcmeCalc calc = new AcmeCalc();
        Integer total = calc.compute(mp);      // local `calc` typed AcmeCalc
        fmt.render(mp);                          // param `fmt` typed AcmeFormatter
        String note = 'StringTrap.run() should not be an edge';
        helper();                                // intra-class self-call
    }
    private void helper() {}

    class Inner {
        void deep() { GlobexAudit.log(); }       // node id keyed on Inner
    }
}
"""


@ast_only
def test_ast_symbol_table_resolves_instance_calls():
    f = _w(Path("/tmp/_acme_apex/AcmeOrderService.cls"), AST_FIXTURE)
    nodes, edges = EX.extract(f)
    et = _et(edges)
    pid = "apexmethod/AcmeOrderService.process"
    cid = "apexclass/AcmeOrderService"
    # `calc.compute()` resolves via the typed local and `fmt.render()` via the
    # typed parameter — regex cannot type a lowercase var.
    assert (pid, "calls", "apexmethod", "AcmeCalc.compute") in et
    assert (cid, "calls", "apexmethod", "AcmeFormatter.render") in et
    # the resolved types also surface as class-level calls
    assert (cid, "calls", "apexclass", "AcmeCalc") in et
    assert (cid, "calls", "apexclass", "AcmeFormatter") in et
    # intra-class self-call still resolves to a method node
    assert (pid, "calls", "apexmethod", "AcmeOrderService.helper") in et


@ast_only
def test_ast_no_comment_or_string_false_positives():
    f = _w(Path("/tmp/_acme_apex/AcmeOrderService.cls"), AST_FIXTURE)
    _, edges = EX.extract(f)
    targets = {e["to_name"] for e in edges}
    # tokens that only appear inside a comment or a string literal must NOT leak
    for ghost in ("ghost", "DangerComment", "StringTrap"):
        assert not any(ghost in t for t in targets), f"{ghost} leaked from comment/string"


@ast_only
def test_ast_nested_class_method_node_id():
    f = _w(Path("/tmp/_acme_apex/AcmeOrderService.cls"), AST_FIXTURE)
    nodes, _ = EX.extract(f)
    ids = {n["id"] for n in nodes if n["type"] == "apexmethod"}
    # the inner-class method keeps an `apexmethod/<InnerClass>.<method>` id
    assert "apexmethod/Inner.deep" in ids
    # outer methods keep the outer class name
    assert "apexmethod/AcmeOrderService.process" in ids


# An @IsTest class exercising other classes (instantiation + static call) plus
# precise SOQL/SOSL reads and a DML write.
AST_TEST_CLASS = """@IsTest
private class AcmeOrderServiceTest {
    @IsTest static void itWorks() {
        AcmeOrderService svc = new AcmeOrderService();
        MeterPoint__c mp = new MeterPoint__c();
        insert mp;
        svc.process(mp);
        AcmeCalc.staticCompute(mp);
        List<MeterPoint__c> rows =
            [SELECT Id, Name FROM MeterPoint__c WHERE Active__c = true];
        List<List<SObject>> hits =
            [FIND 'secretterm' IN ALL FIELDS RETURNING Account(Id), Reading__c(Name)];
    }
}
"""


@ast_only
def test_ast_istest_class_emits_tests_edges():
    f = _w(Path("/tmp/_acme_apex/AcmeOrderServiceTest.cls"), AST_TEST_CLASS)
    _, edges = EX.extract(f)
    et = _et(edges)
    cid = "apexclass/AcmeOrderServiceTest"
    # @IsTest class -> `tests` -> the class(es) it instantiates / exercises
    assert (cid, "tests", "apexclass", "AcmeOrderService") in et   # `new ...`
    assert (cid, "tests", "apexclass", "AcmeCalc") in et            # static call


@ast_only
def test_ast_soql_sosl_reads_and_dml_writes():
    f = _w(Path("/tmp/_acme_apex/AcmeOrderServiceTest.cls"), AST_TEST_CLASS)
    _, edges = EX.extract(f)
    et = _et(edges)
    mid = "apexmethod/AcmeOrderServiceTest.itWorks"
    # SOQL: object + selected fields (precise, structural)
    assert (mid, "reads", "object", "MeterPoint__c") in et
    assert (mid, "reads", "field", "MeterPoint__c.Id") in et
    assert (mid, "reads", "field", "MeterPoint__c.Name") in et
    # a WHERE-clause-only field is NOT mistaken for a selected (read) field
    assert (mid, "reads", "field", "MeterPoint__c.Active__c") not in et
    # SOSL: each RETURNING sObject is a read (term value never inspected)
    assert (mid, "reads", "object", "Account") in et
    assert (mid, "reads", "object", "Reading__c") in et
    # DML write
    assert (mid, "writes", "object", "MeterPoint__c") in et


@ast_only
def test_ast_confidentiality_no_literal_values_emitted():
    """Only names and structure leave the extractor: the SOSL search term and
    string-literal contents never appear in node or edge data."""
    f = _w(Path("/tmp/_acme_apex/AcmeOrderServiceTest.cls"), AST_TEST_CLASS)
    nodes, edges = EX.extract(f)
    blob = repr(nodes) + repr(edges)
    assert "secretterm" not in blob


@ast_only
def test_ast_backend_is_active_by_default():
    """With the grammar installed, `extract` uses the AST backend."""
    assert apexmod._APEX_PARSER is not None


# Forced regex fallback: with the parser flag patched to None, the fallback
# backend still emits the core edges (the guarantee when tree-sitter is absent).
def test_regex_fallback_when_parser_unavailable(monkeypatch):
    monkeypatch.setattr(apexmod, "_APEX_PARSER", None)   # force regex backend
    assert apexmod._APEX_PARSER is None

    f = _w(Path("/tmp/_acme_apex/AcmeMeterPointService.cls"), SERVICE)
    nodes, edges = EX.extract(f)
    ids = _ids(nodes)
    et = _et(edges)

    # class + method nodes still present
    assert "apexclass/AcmeMeterPointService" in ids
    for m in ("getActiveMeterPoints", "recalc", "pushToBilling", "logAccess"):
        assert f"apexmethod/AcmeMeterPointService.{m}" in ids

    cid = "apexclass/AcmeMeterPointService"
    # core structural edges from the regex backend
    assert (cid, "references", "object", "MeterPoint__c") in et
    assert (cid, "extends", "apexclass", "BaseService") in et
    assert (cid, "implements", "apexclass", "Batchable") in et
    assert (cid, "contains", "apexmethod", "AcmeMeterPointService.getActiveMeterPoints") in et
    # intra-class call + per-method read/write still resolved by regex
    assert ("apexmethod/AcmeMeterPointService.getActiveMeterPoints", "calls",
            "apexmethod", "AcmeMeterPointService.logAccess") in et
    assert ("apexmethod/AcmeMeterPointService.getActiveMeterPoints", "reads",
            "object", "MeterPoint__c") in et
    assert ("apexmethod/AcmeMeterPointService.pushToBilling", "writes",
            "object", "BillingEvent__c") in et


def test_regex_fallback_build_graph_resolves(monkeypatch, tmp_path):
    """The regex fallback still produces a clean, resolvable build."""
    monkeypatch.setattr(apexmod, "_APEX_PARSER", None)
    fa = tmp_path / "force-app" / "main" / "default" / "classes"
    _w(fa / "AcmeMeterPointService.cls", SERVICE)
    _w(fa / "BaseService.cls",
       "public abstract class BaseService {\n  public void init(){}\n}\n")
    g = (GraphBuilder()
         .register(EX)
         .register_resolver(*resolvers.default_resolvers())
         .build(tmp_path))
    assert g["errors"] == []
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert ("apexclass/AcmeMeterPointService", "extends", "apexclass/BaseService") in edges
    assert ("apexclass/AcmeMeterPointService", "contains",
            "apexmethod/AcmeMeterPointService.recalc") in edges
