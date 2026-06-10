"""CLI tests — build mode, pipeline mode, and the exit-code contract.

Fictional fixtures only. No network: pipeline runs without --collect.
"""
import json

from graphbuilder.__main__ import main


def _force_app(root):
    fa = root / "force-app" / "main" / "default"
    (fa / "classes").mkdir(parents=True)
    (fa / "classes" / "Foo.cls").write_text(
        "public class Foo { void run() { Acme__c a; } }", "utf-8")
    return fa


def _dump(root):
    d = root / "confluence-dump" / "ENG"
    d.mkdir(parents=True)
    (d / "100.page.json").write_text(json.dumps({
        "id": "100", "title": "Acme Overview", "space": {"key": "ENG"},
        "body": {"storage": {"value":
            'See <a href="https://x.lightning.force.com/lightning/o/Acme__c/list">Acme</a>'}},
    }), "utf-8")
    return root / "confluence-dump"


def test_build_clean_exits_zero(tmp_path, capsys):
    fa = _force_app(tmp_path)
    out = tmp_path / "graph.json"
    assert main([str(fa), "-o", str(out)]) == 0
    assert out.exists()
    assert "errors=0" in capsys.readouterr().err


def test_build_with_recorded_errors_exits_three(tmp_path, capsys):
    fa = _force_app(tmp_path)
    (fa / "classes" / "broken.page.json").write_text("not json {", "utf-8")
    assert main([str(fa), "-o", str(tmp_path / "g.json")]) == 3
    assert "errors=1" in capsys.readouterr().err
    assert (tmp_path / "g.json").exists()          # output still written


def test_pipeline_bundles_both_sources(tmp_path, capsys):
    fa, dump = _force_app(tmp_path), _dump(tmp_path)
    out = tmp_path / "kb"
    code = main(["pipeline", "--salesforce", str(fa), "--confluence-dump", str(dump),
                 "--out", str(out), "--no-zip"])
    assert code == 0
    assert (out / "graph.json").exists() and (out / "manifest.json").exists()
    err = capsys.readouterr().err
    assert "bundle:" in err and "documents=1" in err
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    assert manifest["graph"]["errors"] == 0 and "unresolved" in manifest["graph"]


def test_pipeline_reads_config_file_with_flag_override(tmp_path):
    fa, dump = _force_app(tmp_path), _dump(tmp_path)
    cfg = tmp_path / "pipeline.json"
    cfg.write_text(json.dumps({
        "salesforce": str(fa), "confluence_dump": str(dump),
        "out": str(tmp_path / "from-config"), "zip": False,
    }), "utf-8")
    assert main(["pipeline", "--config", str(cfg)]) == 0
    assert (tmp_path / "from-config" / "graph.json").exists()
    # an explicit flag beats the config value
    assert main(["pipeline", "--config", str(cfg), "--out", str(tmp_path / "flag-out")]) == 0
    assert (tmp_path / "flag-out" / "graph.json").exists()


def test_pipeline_without_sources_is_fatal(capsys):
    assert main(["pipeline"]) == 1
    assert "nothing to do" in capsys.readouterr().err


def test_pipeline_collect_without_dump_is_fatal(tmp_path, capsys):
    assert main(["pipeline", "--collect", "--salesforce", str(_force_app(tmp_path))]) == 1
    assert "--collect needs" in capsys.readouterr().err
