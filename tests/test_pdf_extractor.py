"""PDF extractor tests — extract() + full builds over in-test-built fixtures.

Fixtures are tiny PDFs authored IN-TEST with pypdf's writer (fictional Acme /
MeterPoint content). pypdf is OPTIONAL (the ``pdf`` extra) — every test that
touches a real PDF is gated on its availability with the same skipif pattern
the apex tests use for tree-sitter; the inertness test (absent pypdf ->
``handles()`` False, files skipped silently) runs everywhere via monkeypatch.
Covered: T3 flat text + refs-as-attrs, T1 outline sections with page ranges,
scanned -> ``needs_ocr``, encrypted -> build error entry, no author metadata,
determinism.
"""
import hashlib
import io
import json
from pathlib import Path

import pytest

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
import graphbuilder.extractors.pdf as pdfmod
from graphbuilder.extractors.pdf import PdfExtractor

EX = PdfExtractor()

# pypdf is optional; everything needing a real PDF is asserted only when it is
# installed in this environment (the `pdf` extra), like AST_AVAILABLE/ast_only.
PDF_AVAILABLE = pdfmod._PYPDF is not None
pdf_only = pytest.mark.skipif(not PDF_AVAILABLE, reason="pypdf not installed")

if PDF_AVAILABLE:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def _text_page(writer, *lines):
    """A page whose content stream renders ``lines`` top-down — minimal
    Helvetica text the reader's extract_text() can recover."""
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    ops, y = [], 280
    for line in lines:
        esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"BT /F1 12 Tf 10 {y} Td ({esc}) Tj ET")
        y -= 20
    stream = DecodedStreamObject()
    stream.set_data(" ".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    return page


def _write(tmp: Path, name: str, writer) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as fh:
        writer.write(fh)
    return p


def _outline_pdf(tmp: Path) -> Path:
    """4 marker pages + outline: Overview(p1) > Details(p2), Rollout(p3)."""
    w = PdfWriter()
    _text_page(w, "P1 Acme overview intro")
    _text_page(w, "P2 MeterPoint details body")
    _text_page(w, "P3 rollout steps")
    _text_page(w, "P4 rollout appendix")
    top = w.add_outline_item("Overview", 0)
    w.add_outline_item("Details", 1, parent=top)
    w.add_outline_item("Rollout", 2)
    return _write(tmp, "spec.pdf", w)


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
# optional-dependency gate + handles
# --------------------------------------------------------------------------- #
def test_absent_pypdf_extractor_inert(monkeypatch, tmp_path):
    # without pypdf the module-level flag is None and handles() declines every
    # file — .pdf files are skipped SILENTLY in a build (no nodes, no errors)
    monkeypatch.setattr(pdfmod, "_PYPDF", None)
    assert EX.handles(Path("a/Spec.pdf")) is False
    p = tmp_path / "spec.pdf"
    p.write_bytes(b"%PDF-1.4 never even opened")
    g = _build(p)
    assert g["nodes"] == [] and g["errors"] == []


@pdf_only
def test_handles():
    assert EX.handles(Path("a/Spec.pdf")) is True
    assert EX.handles(Path("a/SPEC.PDF")) is True
    assert EX.handles(Path("a/Spec.docx")) is False
    assert EX.handles(Path("a/101.page.json")) is False


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #
@pdf_only
def test_docfile_identity_hash_id_filename_label(tmp_path):
    w = PdfWriter()
    _text_page(w, "Acme MeterPoint mapping notes")
    p = _write(tmp_path, "Acme Spec.pdf", w)
    nodes, _ = EX.extract(p)
    doc = _ids(nodes)[f"docfile/{_fid(p)}"]               # sha1-12 content id
    assert doc["type"] == "docfile" and doc["label"] == "Acme Spec.pdf"
    assert doc["source"] == "docs" and doc["doc_type"] == "pdf"
    assert doc["page_count"] == 1


@pdf_only
def test_same_content_different_name_same_node_id(tmp_path):
    w = PdfWriter()
    _text_page(w, "Body")
    buf = io.BytesIO()
    w.write(buf)
    a, b = tmp_path / "a.pdf", tmp_path / "sub" / "renamed.pdf"
    b.parent.mkdir()
    a.write_bytes(buf.getvalue())
    b.write_bytes(buf.getvalue())
    na, _ = EX.extract(a)
    nb, _ = EX.extract(b)
    assert na[0]["id"] == nb[0]["id"]                     # rename is a non-event
    assert na[0]["label"] != nb[0]["label"]               # the filename stays visible


# --------------------------------------------------------------------------- #
# T3 — no outline: honest flat, text + refs on the docfile
# --------------------------------------------------------------------------- #
@pdf_only
def test_t3_flat_text_on_docfile_refs_as_attrs(tmp_path):
    w = PdfWriter()
    _text_page(w, "Acme MeterPoint mapping notes",
               "See ACME-1 and field MeterPoint__c",
               "Spec at https://acme.example.invalid/spec")
    p = _write(tmp_path, "flat.pdf", w)
    nodes, edges = EX.extract(p)
    assert [n["type"] for n in nodes] == ["docfile"]      # sections never fabricated
    assert edges == []
    doc = nodes[0]
    assert doc["structure"] == "none" and "needs_ocr" not in doc
    for marker in ("Acme MeterPoint mapping notes", "ACME-1", "MeterPoint__c"):
        assert marker in doc["text"]
    assert doc["jira_keys"] == ["ACME-1"]                 # attrs, never edges
    assert doc["sf_names"] == ["MeterPoint__c"]
    assert doc["urls"] == ["https://acme.example.invalid/spec"]


# --------------------------------------------------------------------------- #
# T1 — outline/bookmarks: declared section tree with page ranges
# --------------------------------------------------------------------------- #
@pdf_only
def test_t1_outline_tree_levels_page_ranges_edges(tmp_path):
    p = _outline_pdf(tmp_path)
    nodes, edges = EX.extract(p)
    fid = _fid(p)
    ids = _ids(nodes)
    doc = ids[f"docfile/{fid}"]
    assert doc["structure"] == "declared" and doc["page_count"] == 4
    s1, s2, s3 = (ids[f"docsection/{fid}#{n}"] for n in (1, 2, 3))
    assert (s1["label"], s2["label"], s3["label"]) == ("Overview", "Details", "Rollout")
    assert (s1["level"], s2["level"], s3["level"]) == (1, 2, 1)
    assert all("confidence" not in s for s in (s1, s2, s3))   # outline = declared (T1)
    # a section runs to the page before the next same-or-higher-level entry;
    # the parent's range spans its subsection, the last runs to the end
    assert (s1["page_start"], s1["page_end"]) == (1, 2)
    assert (s2["page_start"], s2["page_end"]) == (2, 2)
    assert (s3["page_start"], s3["page_end"]) == (3, 4)
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert (f"docfile/{fid}", "contains", "docsection", f"{fid}#1") in et
    assert (f"docsection/{fid}#2", "child-of", "docsection", f"{fid}#1") in et
    assert (f"docfile/{fid}", "contains", "docsection", f"{fid}#3") in et


@pdf_only
def test_t1_section_text_covers_its_page_range(tmp_path):
    p = _outline_pdf(tmp_path)
    nodes, _ = EX.extract(p)
    s = _by_label(nodes)
    assert "P1" in s["Overview"]["text"] and "P2" in s["Overview"]["text"]
    assert "P3" not in s["Overview"]["text"]
    assert "P2" in s["Details"]["text"]
    assert "P1" not in s["Details"]["text"] and "P3" not in s["Details"]["text"]
    assert "P3" in s["Rollout"]["text"] and "P4" in s["Rollout"]["text"]


@pdf_only
def test_pages_before_first_bookmark_stay_on_docfile(tmp_path):
    w = PdfWriter()
    _text_page(w, "P1 cover note before any bookmark")
    _text_page(w, "P2 main body")
    w.add_outline_item("Main", 1)                         # outline starts on page 2
    p = _write(tmp_path, "pre.pdf", w)
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert "P1" in doc["text"] and "P2" not in doc["text"]
    sec = _by_label(nodes)["Main"]
    assert (sec["page_start"], sec["page_end"]) == (2, 2)
    assert "P2" in sec["text"]


# --------------------------------------------------------------------------- #
# scanned + encrypted + corrupt
# --------------------------------------------------------------------------- #
@pdf_only
def test_scanned_pages_without_text_flag_needs_ocr(tmp_path):
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)               # image-only stand-ins:
    w.add_blank_page(width=200, height=200)               # pages with no text layer
    p = _write(tmp_path, "scan.pdf", w)
    nodes, edges = EX.extract(p)
    assert [n["type"] for n in nodes] == ["docfile"] and edges == []
    doc = nodes[0]
    assert doc["needs_ocr"] is True and doc["page_count"] == 2
    assert "text" not in doc                              # no empty-text pretence
    assert doc["structure"] == "none"


@pdf_only
def test_encrypted_pdf_recorded_in_errors(tmp_path):
    w = PdfWriter()
    _text_page(w, "Acme confidential body")
    w.encrypt("inzorg-test")
    p = _write(tmp_path, "locked.pdf", w)
    g = _build(p)                                         # raised, never skipped
    assert g["nodes"] == [] and len(g["errors"]) == 1
    err = g["errors"][0]
    assert err["source"] == "docs" and err["path"] == "locked.pdf"
    assert "encrypted" in err["error"]


@pdf_only
def test_broken_file_recorded_in_errors(tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"this is not a pdf at all")
    g = _build(p)
    assert g["nodes"] == [] and len(g["errors"]) == 1
    assert g["errors"][0]["source"] == "docs"


# --------------------------------------------------------------------------- #
# metadata confidentiality; full-build resolution; determinism
# --------------------------------------------------------------------------- #
@pdf_only
def test_metadata_title_modified_but_never_authors(tmp_path):
    w = PdfWriter()
    _text_page(w, "Body")
    w.add_metadata({"/Title": "Acme PDF Spec",
                    "/Author": "Acme Author",
                    "/Creator": "Acme Author Tool",
                    "/ModDate": "D:20260501100000+00'00'"})
    p = _write(tmp_path, "meta.pdf", w)
    nodes, _ = EX.extract(p)
    doc = nodes[0]
    assert doc["title"] == "Acme PDF Spec"
    assert doc["modified"].startswith("2026-05-01T10:00:00")
    assert "Acme Author" not in json.dumps(nodes)         # author names never captured


@pdf_only
def test_full_build_resolves_all_edges_no_stubs(tmp_path):
    p = _outline_pdf(tmp_path)
    g = _build(p)
    fid = _fid(p)
    assert g["errors"] == [] and g["unresolved"] == []
    assert not any(n.get("external") for n in g["nodes"])
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert (f"docfile/{fid}", "contains", f"docsection/{fid}#1") in edges
    assert (f"docsection/{fid}#2", "child-of", f"docsection/{fid}#1") in edges
    # source_path stamped by the core on every node
    assert all(n.get("source_path") for n in g["nodes"])


@pdf_only
def test_double_build_is_identical(tmp_path):
    p = _outline_pdf(tmp_path)
    assert EX.extract(p) == EX.extract(p)                 # same file -> identical graph
