"""Docx extractor tests — extract() + full builds over in-test-built fixtures.

Fixtures are minimal OOXML zips authored with stdlib ``zipfile`` (fictional
Acme / MeterPoint content, Polish strings for unicode coverage). They cover the
three structure tiers (T1 declared styles/outline, T2 bold-short heuristic,
T3 honest flat), the confidentiality rules (no author names; refs as attrs,
never edges) and the corrupt-file -> ``errors`` path.
"""
import hashlib
import json
import zipfile
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.docx import DocxExtractor

EX = DocxExtractor()

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)


def _p(text, style=None, outline=None, bold=False):
    bits = ""
    if style:
        bits += f'<w:pStyle w:val="{style}"/>'
    if outline is not None:
        bits += f'<w:outlineLvl w:val="{outline}"/>'
    ppr = f"<w:pPr>{bits}</w:pPr>" if bits else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'{ppr}<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


def _para(text, **kw):
    return f"<w:p>{_p(text, **kw)}</w:p>"


def _tbl(*rows):
    body = "".join(
        "<w:tr>" + "".join(
            f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
        for row in rows)
    return f"<w:tbl>{body}</w:tbl>"


def _document(*blocks):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document {W}><w:body>{"".join(blocks)}</w:body></w:document>')


def _docx(tmp: Path, name: str, document: str, core=None, rels=None) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("word/document.xml", document)
        if core:
            z.writestr("docProps/core.xml", core)
        if rels:
            z.writestr("word/_rels/document.xml.rels", rels)
    return p


def _ids(nodes):
    return {n["id"]: n for n in nodes}


def _by_label(nodes):
    return {n["label"]: n for n in nodes if n["type"] == "docsection"}


def _fid(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _build(*paths):
    return (GraphBuilder().register(EX)
            .register_resolver(*resolvers.default_resolvers())
            .build_files(list(paths)))


# --------------------------------------------------------------------------- #
# handles + identity
# --------------------------------------------------------------------------- #
def test_handles():
    assert EX.handles(Path("a/Spec.docx")) is True
    assert EX.handles(Path("a/SPEC.DOCX")) is True
    assert EX.handles(Path("a/Spec.doc")) is False        # legacy binary rejected
    assert EX.handles(Path("a/Sheet.xlsx")) is False
    assert EX.handles(Path("a/101.page.json")) is False


def test_docfile_identity_hash_id_filename_label(tmp_path):
    p = _docx(tmp_path, "Acme Spec.docx", _document(_para("MeterPoint mapping notes.")))
    nodes, _ = EX.extract(p)
    doc = _ids(nodes)[f"docfile/{_fid(p)}"]               # sha1-12 content id
    assert doc["type"] == "docfile" and doc["label"] == "Acme Spec.docx"
    assert doc["source"] == "docs" and doc["doc_type"] == "docx"


def test_same_content_different_name_same_node_id(tmp_path):
    document = _document(_para("Body", style="Heading1"))
    a = _docx(tmp_path, "a.docx", document)
    b = _docx(tmp_path / "sub", "renamed.docx", document)
    na, _ = EX.extract(a)
    nb, _ = EX.extract(b)
    assert na[0]["id"] == nb[0]["id"]                     # rename is a non-event
    assert na[0]["label"] != nb[0]["label"]               # the filename stays visible


# --------------------------------------------------------------------------- #
# T1 — declared headings
# --------------------------------------------------------------------------- #
def test_t1_heading_tree_nodes_and_edges(tmp_path):
    p = _docx(tmp_path, "spec.docx", _document(
        _para("Overview", style="Heading1"),
        _para("Acme platform intro."),
        _para("Punkt pomiarowy", style="Heading2"),       # Polish heading
        _para("Szczegóły konfiguracji MeterPoint."),
        _para("Rollout", style="Heading1"),
        _para("Final step."),
    ))
    nodes, edges = EX.extract(p)
    fid = _fid(p)
    ids = _ids(nodes)
    doc = ids[f"docfile/{fid}"]
    assert doc["structure"] == "declared"
    s1, s2, s3 = (ids[f"docsection/{fid}#{n}"] for n in (1, 2, 3))
    assert (s1["label"], s2["label"], s3["label"]) == ("Overview", "Punkt pomiarowy", "Rollout")
    assert (s1["level"], s2["level"], s3["level"]) == (1, 2, 1)
    assert all("confidence" not in s for s in (s1, s2, s3))   # declared = trusted default
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert (f"docfile/{fid}", "contains", "docsection", f"{fid}#1") in et
    assert (f"docsection/{fid}#2", "child-of", "docsection", f"{fid}#1") in et
    assert (f"docfile/{fid}", "contains", "docsection", f"{fid}#3") in et


def test_t1_section_text_spans_to_next_same_or_higher_heading(tmp_path):
    p = _docx(tmp_path, "spec.docx", _document(
        _para("Overview", style="Heading1"),
        _para("Intro paragraph."),
        _para("Details", style="Heading2"),
        _para("Detail body."),
        _para("Rollout", style="Heading1"),
        _para("Final."),
    ))
    nodes, _ = EX.extract(p)
    s = _by_label(nodes)
    # the parent's text spans its subsection's body (FTS over a section finds
    # everything under it); the sub-heading LINE itself is structure, not body
    assert s["Overview"]["text"] == "Intro paragraph.\nDetail body."
    assert s["Details"]["text"] == "Detail body."
    assert s["Rollout"]["text"] == "Final."


def test_t1_outline_lvl_alone_is_declared(tmp_path):
    p = _docx(tmp_path, "o.docx", _document(
        _para("Zakres", outline=0),                       # outlineLvl 0 -> level 1
        _para("Treść rozdziału."),
        _para("Mapowanie", outline=1),                    # outlineLvl 1 -> level 2
        _para("Body."),
    ))
    nodes, _ = EX.extract(p)
    assert _ids(nodes)[next(iter(_ids(nodes)))]["structure"] == "declared"
    s = _by_label(nodes)
    assert s["Zakres"]["level"] == 1 and s["Mapowanie"]["level"] == 2


def test_t1_title_style_is_level_one(tmp_path):
    p = _docx(tmp_path, "t.docx", _document(
        _para("Acme Integration Spec", style="Title"),
        _para("Preface."),
    ))
    nodes, _ = EX.extract(p)
    s = _by_label(nodes)["Acme Integration Spec"]
    assert s["level"] == 1 and s["text"] == "Preface."


def test_preamble_before_first_heading_stays_on_docfile(tmp_path):
    p = _docx(tmp_path, "pre.docx", _document(
        _para("Cover note before any heading."),
        _para("Overview", style="Heading1"),
        _para("Body."),
    ))
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["text"] == "Cover note before any heading."
    assert _by_label(nodes)["Overview"]["text"] == "Body."


# --------------------------------------------------------------------------- #
# T2 — bold-short heuristic (only when zero T1 headings)
# --------------------------------------------------------------------------- #
def test_t2_bold_short_paragraphs_become_heuristic_sections(tmp_path):
    p = _docx(tmp_path, "adhoc.docx", _document(
        _para("Zakres", bold=True),
        _para("Opis zakresu integracji Acme."),
        _para("Punkt pomiarowy", bold=True),
        _para("Konfiguracja MeterPoint."),
    ))
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["structure"] == "heuristic"
    s = _by_label(nodes)
    assert set(s) == {"Zakres", "Punkt pomiarowy"}
    for sec in s.values():
        assert sec["confidence"] == "heuristic" and sec["level"] == 1
    assert s["Zakres"]["text"] == "Opis zakresu integracji Acme."


def test_t2_thresholds_reject_long_trailing_period_and_plain(tmp_path):
    p = _docx(tmp_path, "no.docx", _document(
        _para("B" * 80, bold=True),                       # >= 80 chars: not a title
        _para("Bold but a sentence.", bold=True),         # trailing period: prose
        _para("Short plain line"),                        # not bold
    ))
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["structure"] == "none"                     # nothing qualified
    assert not any(n["type"] == "docsection" for n in nodes)


def test_t2_suppressed_when_any_t1_heading_exists(tmp_path):
    p = _docx(tmp_path, "mix.docx", _document(
        _para("Overview", style="Heading1"),
        _para("Ważne", bold=True),                        # bold-short, but T1 present
        _para("Body."),
    ))
    nodes, _ = EX.extract(p)
    s = _by_label(nodes)
    assert set(s) == {"Overview"}                         # tiers never mix in one doc
    assert s["Overview"]["text"] == "Ważne\nBody."        # the bold line stays body text


# --------------------------------------------------------------------------- #
# T3 — honest flat
# --------------------------------------------------------------------------- #
def test_t3_flat_document_text_on_docfile_no_sections(tmp_path):
    p = _docx(tmp_path, "flat.docx", _document(
        _para("First plain paragraph."),
        _para("Second plain paragraph."),
    ))
    nodes, edges = EX.extract(p)
    assert [n["type"] for n in nodes] == ["docfile"]      # sections never fabricated
    doc = nodes[0]
    assert doc["structure"] == "none"
    assert doc["text"] == "First plain paragraph.\nSecond plain paragraph."
    assert edges == []


def test_empty_document_no_text_attr(tmp_path):
    p = _docx(tmp_path, "empty.docx", _document())
    nodes, _ = EX.extract(p)
    doc = nodes[0]
    assert doc["structure"] == "none" and "text" not in doc   # attrs only when non-empty


# --------------------------------------------------------------------------- #
# tables, refs, core props
# --------------------------------------------------------------------------- #
def test_word_table_first_row_becomes_columns_on_owner(tmp_path):
    p = _docx(tmp_path, "tbl.docx", _document(
        _tbl(["Plik", "Status"], ["mapping.csv", "done"]),     # before any heading
        _para("Mapping", style="Heading1"),
        _tbl(["Pole Acme", "Pole SAP"], ["MeterPoint__c", "EQUI"]),
        _tbl(["Ignored", "SecondTable"]),                 # first table per owner wins
    ))
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["columns"] == ["Plik", "Status"]           # unowned table -> docfile
    sec = _by_label(nodes)["Mapping"]
    assert sec["columns"] == ["Pole Acme", "Pole SAP"]
    # header NAMES only — the data rows never enter the graph
    dump = json.dumps(nodes, ensure_ascii=False)
    assert "mapping.csv" not in dump and "EQUI" not in dump


def test_detected_refs_are_attrs_never_edges(tmp_path):
    p = _docx(tmp_path, "refs.docx", _document(
        _para("Sync", style="Heading1"),
        _para("See ACME-101 and ACME-7; field MeterPoint__c "
              "via https://wiki.example.invalid/x/abc."),
    ))
    nodes, edges = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["jira_keys"] == ["ACME-101", "ACME-7"]
    assert doc["sf_names"] == ["MeterPoint__c"]
    assert doc["urls"] == ["https://wiki.example.invalid/x/abc"]   # trailing "." stripped
    assert {e["to_kind"] for e in edges} <= {"docsection"}    # never jiraissue/object edges


def test_hyperlink_rels_targets_join_urls(tmp_path):
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
            '/2006/relationships/hyperlink" Target="https://acme.example.invalid/runbook"'
            ' TargetMode="External"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument'
            '/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>")
    p = _docx(tmp_path, "links.docx", _document(_para("Linked text.")), rels=rels)
    nodes, _ = EX.extract(p)
    assert nodes[0]["urls"] == ["https://acme.example.invalid/runbook"]


def test_core_props_title_modified_but_never_authors(tmp_path):
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties'
            ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
            ' xmlns:dcterms="http://purl.org/dc/terms/">'
            "<dc:title>Specyfikacja punktów pomiarowych</dc:title>"
            "<dc:creator>Acme Author</dc:creator>"
            "<cp:lastModifiedBy>Acme Author</cp:lastModifiedBy>"
            "<dcterms:modified>2026-05-01T10:00:00Z</dcterms:modified>"
            "</cp:coreProperties>")
    p = _docx(tmp_path, "core.docx", _document(_para("Body.")), core=core)
    nodes, _ = EX.extract(p)
    doc = nodes[0]
    assert doc["title"] == "Specyfikacja punktów pomiarowych"
    assert doc["modified"] == "2026-05-01T10:00:00Z"
    assert "Acme Author" not in json.dumps(nodes)         # author names never captured


def test_bom_junk_before_xml_declaration_tolerated(tmp_path):
    doc_xml = "﻿\n" + _document(_para("Overview", style="Heading1"), _para("Body."))
    p = _docx(tmp_path, "bom.docx", doc_xml)
    nodes, _ = EX.extract(p)
    assert _by_label(nodes)["Overview"]["text"] == "Body."


# --------------------------------------------------------------------------- #
# corrupt files -> build errors; full-build resolution; determinism
# --------------------------------------------------------------------------- #
def test_broken_zip_recorded_in_errors(tmp_path):
    p = tmp_path / "broken.docx"
    p.write_bytes(b"this is not a zip archive at all")
    g = _build(p)
    assert g["nodes"] == [] and len(g["errors"]) == 1
    assert g["errors"][0]["source"] == "docs" and g["errors"][0]["path"] == "broken.docx"


def test_broken_inner_xml_recorded_in_errors(tmp_path):
    p = tmp_path / "badxml.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("word/document.xml", "<w:document <<< not xml")
    g = _build(p)
    assert g["nodes"] == [] and len(g["errors"]) == 1


def test_full_build_resolves_all_edges_no_stubs(tmp_path):
    p = _docx(tmp_path, "spec.docx", _document(
        _para("Overview", style="Heading1"),
        _para("Body."),
        _para("Details", style="Heading2"),
        _para("More."),
    ))
    g = _build(p)
    fid = _fid(p)
    assert g["errors"] == [] and g["unresolved"] == []
    assert not any(n.get("external") for n in g["nodes"])
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert (f"docfile/{fid}", "contains", f"docsection/{fid}#1") in edges
    assert (f"docsection/{fid}#2", "child-of", f"docsection/{fid}#1") in edges
    # source_path stamped by the core on every node
    assert all(n.get("source_path") for n in g["nodes"])


def test_double_build_is_identical(tmp_path):
    p = _docx(tmp_path, "det.docx", _document(
        _para("Overview", style="Heading1"),
        _para("Body ACME-1 MeterPoint__c."),
        _tbl(["Kolumna", "Wartość"]),
        _para("Details", style="Heading2"),
        _para("More."),
    ))
    first = EX.extract(p)
    second = EX.extract(p)
    assert first == second                                # same file -> identical graph
