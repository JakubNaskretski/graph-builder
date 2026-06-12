"""The public two-phase API (extract_files + resolve_extracted) must equal
build_files — callers that keep the per-file extraction results (a digest
building one record per source file) get the same graph without a second
extraction pass.
"""
from graphbuilder.core import GraphBuilder
from graphbuilder.resolvers import default_resolvers


def _builder():
    from graphbuilder.extractors import all_extractors
    return (GraphBuilder().register(*all_extractors())
            .register_resolver(*default_resolvers()))


def _mini_org(tmp_path):
    cls = tmp_path / "force-app" / "main" / "default" / "classes"
    cls.mkdir(parents=True)
    (cls / "AcmeSvc.cls").write_text(
        "public class AcmeSvc {\n"
        "    public void run() { AcmeOther.go(); insert new Acme__c(); }\n"
        "}\n", "utf-8")
    (cls / "Broken.cls").write_text("public class Broken {", "utf-8")
    return tmp_path


def test_two_phase_equals_build_files(tmp_path):
    paths = sorted(p for p in _mini_org(tmp_path).rglob("*") if p.is_file())
    one_shot = _builder().build_files(paths)

    b = _builder()
    extracted, errors = b.extract_files(paths)
    assert [p.name for p, _, _ in extracted]      # per-file results kept
    assert all(isinstance(n, list) and isinstance(e, list)
               for _, n, e in extracted)
    two_phase = b.resolve_extracted(extracted, errors)
    assert two_phase == one_shot


def test_resolve_extracted_defaults_errors(tmp_path):
    b = _builder()
    extracted, _ = b.extract_files(
        sorted(p for p in _mini_org(tmp_path).rglob("*") if p.is_file()))
    g = b.resolve_extracted(extracted)            # errors optional
    assert g["errors"] == []
    assert any(n["id"] == "apexclass/AcmeSvc" for n in g["nodes"])
