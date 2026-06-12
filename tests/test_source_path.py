"""Every node a build emits is traceable to its base source file via the
``source_path`` attr stamped in pass 1 (extract). External stubs — created by
resolvers, not files — deliberately carry none."""
from graphbuilder import build_graph
from graphbuilder.extractors import all_extractors
from graphbuilder.core import GraphBuilder
from graphbuilder.resolvers import default_resolvers


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def _repo(tmp_path):
    fa = tmp_path / "force-app" / "main" / "default"
    _w(fa / "classes" / "MeterPointService.cls",
       "public class MeterPointService {\n  public static void run() {}\n}\n")
    _w(fa / "triggers" / "MeterPointTrigger.trigger",
       "trigger MeterPointTrigger on MeterPoint__c (after insert) {\n"
       "  MeterPointService.run();\n}\n")
    return tmp_path


def test_build_stamps_repo_relative_posix_source_path(tmp_path):
    g = build_graph(_repo(tmp_path))
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id["apexclass/MeterPointService"]["source_path"] == \
        "force-app/main/default/classes/MeterPointService.cls"
    assert by_id["trigger/MeterPointTrigger"]["source_path"] == \
        "force-app/main/default/triggers/MeterPointTrigger.trigger"
    # child nodes inherit the file that declared them
    assert by_id["apexmethod/MeterPointService.run"]["source_path"].endswith(
        "classes/MeterPointService.cls")
    # every non-external node is traceable; stubs are resolver-made, no file
    for n in g["nodes"]:
        if n.get("external"):
            assert "source_path" not in n
        else:
            assert n["source_path"], n["id"]


def test_build_files_without_root_keeps_given_path(tmp_path):
    repo = _repo(tmp_path)
    cls = repo / "force-app" / "main" / "default" / "classes" / "MeterPointService.cls"
    gb = GraphBuilder().register(*all_extractors())
    gb.register_resolver(*default_resolvers())
    g = gb.build_files([cls])
    node = next(n for n in g["nodes"] if n["id"] == "apexclass/MeterPointService")
    assert node["source_path"] == cls.as_posix()


def test_extractor_supplied_source_path_wins(tmp_path):
    class Dummy:
        source = "dummy"
        def handles(self, path):
            return path.suffix == ".dmy"
        def extract(self, path):
            return [{"id": "object/Dmy", "type": "object", "label": "Dmy",
                     "source_path": "custom/origin"}], []

    f = tmp_path / "a.dmy"
    f.write_text("x", "utf-8")
    gb = GraphBuilder().register(Dummy())
    gb.register_resolver(*default_resolvers())
    g = gb.build_files([f], root=tmp_path)
    node = next(n for n in g["nodes"] if n["id"] == "object/Dmy")
    assert node["source_path"] == "custom/origin"   # setdefault: extractor wins
