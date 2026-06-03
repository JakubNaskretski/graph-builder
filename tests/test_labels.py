"""Tests for the Custom Labels extractor (synthetic, fictional names only)."""
from __future__ import annotations

import textwrap
from pathlib import Path

from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.labels import LabelExtractor
from graphbuilder.resolvers import default_resolvers


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


GOOD = """\
<?xml version="1.0" encoding="UTF-8"?>
<CustomLabels xmlns="http://soap.sforce.com/2006/04/metadata">
    <labels>
        <fullName>Acme_Welcome</fullName>
        <categories>Onboarding</categories>
        <language>en_US</language>
        <protected>true</protected>
        <shortDescription>Acme welcome banner</shortDescription>
        <value>Welcome to Acme, valued customer!</value>
    </labels>
    <labels>
        <fullName>MeterPoint_Error</fullName>
        <categories>Errors</categories>
        <language>pl</language>
        <protected>false</protected>
        <value>The MeterPoint reading could not be saved.</value>
    </labels>
    <labels>
        <fullName>Globex_NoMeta</fullName>
        <value>Globex bare label with no category/language</value>
    </labels>
</CustomLabels>
"""


def _extract(tmp_path: Path, name: str, body: str):
    return LabelExtractor().extract(_write(tmp_path, name, body))


def test_handles():
    ex = LabelExtractor()
    assert ex.handles(Path("CustomLabels.labels-meta.xml"))
    assert not ex.handles(Path("Foo.object-meta.xml"))
    assert not ex.handles(Path("Foo.labels"))


def test_emits_label_nodes_with_category_and_language(tmp_path):
    nodes, edges = _extract(tmp_path, "CustomLabels.labels-meta.xml", GOOD)
    assert edges == []  # no outgoing edges

    by_id = {n["id"]: n for n in nodes}
    assert set(by_id) == {
        "label/Acme_Welcome",
        "label/MeterPoint_Error",
        "label/Globex_NoMeta",
    }

    welcome = by_id["label/Acme_Welcome"]
    assert welcome["type"] == "label"
    assert welcome["label"] == "Acme_Welcome"
    assert welcome["category"] == "Onboarding"
    assert welcome["language"] == "en_US"

    err = by_id["label/MeterPoint_Error"]
    assert err["category"] == "Errors"
    assert err["language"] == "pl"


def test_value_text_is_never_emitted(tmp_path):
    """Confidentiality: the <value> text must not appear in any node attr."""
    nodes, _ = _extract(tmp_path, "CustomLabels.labels-meta.xml", GOOD)
    for n in nodes:
        for v in n.values():
            assert "Welcome to Acme" not in str(v)
            assert "could not be saved" not in str(v)
            assert "bare label" not in str(v)
        assert "value" not in n  # no 'value' attr key at all


def test_missing_optional_attrs_are_omitted(tmp_path):
    nodes, _ = _extract(tmp_path, "CustomLabels.labels-meta.xml", GOOD)
    bare = next(n for n in nodes if n["id"] == "label/Globex_NoMeta")
    assert "category" not in bare
    assert "language" not in bare


def test_malformed_and_oddities_are_skipped(tmp_path):
    body = """\
        <?xml version="1.0" encoding="UTF-8"?>
        <CustomLabels xmlns="http://soap.sforce.com/2006/04/metadata">
            <labels>
                <fullName>Acme_Dup</fullName>
                <categories>A</categories>
            </labels>
            <labels>
                <fullName>Acme_Dup</fullName>
                <categories>B</categories>
            </labels>
            <labels>
                <categories>NoName</categories>
                <value>entry without a fullName is skipped</value>
            </labels>
        </CustomLabels>
    """
    nodes, edges = _extract(tmp_path, "Dup.labels-meta.xml", body)
    ids = [n["id"] for n in nodes]
    assert ids == ["label/Acme_Dup"]  # deduped; nameless entry dropped
    assert edges == []
    # first occurrence wins
    assert nodes[0]["category"] == "A"


def test_broken_xml_does_not_raise(tmp_path):
    nodes, edges = _extract(tmp_path, "Broken.labels-meta.xml", "<CustomLabels><labels>")
    assert nodes == []
    assert edges == []


def test_build_creates_real_label_nodes(tmp_path):
    """End-to-end through the core: stub `label` kind is now backed by real nodes."""
    _write(tmp_path, "CustomLabels.labels-meta.xml", GOOD)
    result = (
        GraphBuilder()
        .register(LabelExtractor())
        .register_resolver(*default_resolvers())
        .build(tmp_path)
    )
    by_id = {n["id"]: n for n in result["nodes"]}
    assert "label/Acme_Welcome" in by_id
    # real node, not an external stub
    assert by_id["label/Acme_Welcome"].get("external") is not True
    assert by_id["label/Acme_Welcome"]["category"] == "Onboarding"
    assert result["edges"] == []


def test_namespace_free_document(tmp_path):
    """Some exports drop the default namespace — local-name matching still works."""
    body = """\
        <CustomLabels>
            <labels>
                <fullName>Globex_Plain</fullName>
                <categories>Misc</categories>
                <language>en_US</language>
                <value>secret value text</value>
            </labels>
        </CustomLabels>
    """
    nodes, _ = _extract(tmp_path, "Plain.labels-meta.xml", body)
    assert len(nodes) == 1
    n = nodes[0]
    assert n["id"] == "label/Globex_Plain"
    assert n["category"] == "Misc"
    assert n["language"] == "en_US"
    assert "secret value text" not in str(n)
