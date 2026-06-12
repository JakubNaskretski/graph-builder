"""Pptx extractor tests — extract() + full builds over in-test-built fixtures.

Fixtures are minimal OOXML zips authored with stdlib ``zipfile`` (fictional
Acme / MeterPoint content). They cover: slide order + titles + body text;
speaker notes captured; table first-row columns (data rows absent); chart
series + categories + title captured and numeric values absent from all node
attrs; sections → declared tier + docsection wiring; sectionless → flat
docfile→slide wiring + structure "none"; .pptm handled, .ppt rejected by
handles(); detect_refs finds ACME-1234 jira key and Foo__c sf name in slide
text; corrupt file raises; image-only slide yields a slide node with no text
and nothing image-related anywhere.
"""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.pptx import PptxExtractor

EX = PptxExtractor()

# --------------------------------------------------------------------------- #
# Namespace constants and shared XML fragments
# --------------------------------------------------------------------------- #
PK = "http://schemas.openxmlformats.org/package/2006/relationships"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"

PPTX_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/ppt/presentation.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    "</Types>"
)

PPTM_CT = PPTX_CT.replace(
    "presentationml.presentation.main+xml",
    "presentationml.presentation.main+xml",  # same content type, just .pptm suffix
)

CORE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<cp:coreProperties'
    ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:dcterms="http://purl.org/dc/terms/">'
    "<dc:title>Acme Integration Overview</dc:title>"
    "<dc:creator>Acme Author</dc:creator>"
    "<dcterms:modified>2026-05-15T09:00:00Z</dcterms:modified>"
    "</cp:coreProperties>"
)


# --------------------------------------------------------------------------- #
# Minimal PPTX builder helpers
# --------------------------------------------------------------------------- #

def _presentation(slide_rids, sections=None):
    """Build ppt/presentation.xml.

    ``slide_rids``: ordered list of r:id strings (e.g. ["rId1", "rId2"]).
    ``sections``: optional list of (name, [member_rIds]) for p14 sections.
    """
    sld_id_els = "".join(
        f'<p:sldId id="{i + 256}" r:id="{rid}"/>'
        for i, rid in enumerate(slide_rids)
    )
    ext_lst = ""
    if sections:
        sec_els = ""
        for s_ord, (s_name, s_rids) in enumerate(sections):
            members = "".join(
                f'<p14:sldId r:id="{r}"/>' for r in s_rids
            )
            sec_els += (
                f'<p14:section xmlns:p14="{P14_NS}" name="{s_name}">'
                f"<p14:sldIdLst>{members}</p14:sldIdLst>"
                "</p14:section>"
            )
        ext_lst = (
            f'<p:extLst>'
            f'<p:ext uri="{{521415D9-36F7-43E2-AB2F-B90AF26B5E84}}">'
            f'<p14:sectionLst xmlns:p14="{P14_NS}">{sec_els}</p14:sectionLst>'
            "</p:ext>"
            "</p:extLst>"
        )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:presentation xmlns:p="{P_NS}" xmlns:r="{RNS}">'
        f'<p:sldIdLst>{sld_id_els}{ext_lst}</p:sldIdLst>'
        f"</p:presentation>"
    )


def _prs_rels(*slide_parts):
    """Build ppt/_rels/presentation.xml.rels with one entry per slide part."""
    entries = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{RNS}/slide" Target="{part}"/>'
        for i, part in enumerate(slide_parts)
    )
    return f'<Relationships xmlns="{PK}">{entries}</Relationships>'


def _slide(title=None, body_paras=None, table_rows=None):
    """Build a minimal slide XML.

    ``title``: text for the title placeholder.
    ``body_paras``: list of strings for the body text box.
    ``table_rows``: list of lists of cell strings (first row = header).
    """
    shapes = ""

    if title is not None:
        shapes += (
            f'<p:sp xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
            f'<p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
            f'<p:txBody>'
            f'<a:p><a:r><a:t>{title}</a:t></a:r></a:p>'
            f"</p:txBody></p:sp>"
        )

    if body_paras:
        para_xml = "".join(
            f'<a:p xmlns:a="{A_NS}"><a:r><a:t>{p}</a:t></a:r></a:p>'
            for p in body_paras
        )
        shapes += (
            f'<p:sp xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
            f"<p:nvSpPr><p:nvPr/></p:nvSpPr>"
            f"<p:txBody>{para_xml}</p:txBody>"
            "</p:sp>"
        )

    if table_rows:
        row_xml = ""
        for row in table_rows:
            cells = "".join(
                f'<a:tc xmlns:a="{A_NS}"><a:txBody>'
                f'<a:p><a:r><a:t>{c}</a:t></a:r></a:p>'
                f"</a:txBody></a:tc>"
                for c in row
            )
            row_xml += f'<a:tr xmlns:a="{A_NS}">{cells}</a:tr>'
        shapes += (
            f'<p:graphicFrame xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
            f"<p:nvGraphicFramePr/>"
            f'<a:graphic><a:graphicData>'
            f'<a:tbl>{row_xml}</a:tbl>'
            f"</a:graphicData></a:graphic>"
            "</p:graphicFrame>"
        )

    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
        f"<p:cSld><p:spTree>{shapes}</p:spTree></p:cSld>"
        "</p:sld>"
    )


def _slide_rels(*rels):
    """Build a slide rels file: ``rels`` is list of (rId, type_suffix, target)."""
    entries = "".join(
        f'<Relationship Id="{rid}" Type="{RNS}/{rtype}" Target="{target}"/>'
        for rid, rtype, target in rels
    )
    return f'<Relationships xmlns="{PK}">{entries}</Relationships>'


def _notes_slide(text):
    """A minimal notesSlide part carrying ``text``."""
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:notes xmlns:p="{P_NS}" xmlns:a="{A_NS}">'
        f'<p:cSld><p:spTree>'
        f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
        f'<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>'
        f"</p:sp>"
        f"</p:spTree></p:cSld>"
        "</p:notes>"
    )


def _chart_xml(title=None, series=None, categories=None, numeric_vals=None):
    """Minimal chart1.xml with optional title, series names, categories, and
    numeric values (which should NOT appear in any output node)."""
    title_xml = ""
    if title:
        title_xml = (
            f'<c:title xmlns:c="{C_NS}">'
            f'<c:tx><c:rich><a:p xmlns:a="{A_NS}"><a:r><a:t>{title}</a:t></a:r></a:p>'
            f"</c:rich></c:tx></c:title>"
        )

    series_xml = ""
    for s_name in (series or []):
        cat_xml = ""
        if categories:
            pts = "".join(
                f'<c:pt xmlns:c="{C_NS}" idx="{i}"><c:v>{lbl}</c:v></c:pt>'
                for i, lbl in enumerate(categories)
            )
            cat_xml = (
                f'<c:cat xmlns:c="{C_NS}"><c:strRef><c:strCache>{pts}</c:strCache>'
                f"</c:strRef></c:cat>"
            )
        val_xml = ""
        if numeric_vals:
            npts = "".join(
                f'<c:pt xmlns:c="{C_NS}" idx="{i}"><c:v>{v}</c:v></c:pt>'
                for i, v in enumerate(numeric_vals)
            )
            val_xml = (
                f'<c:val xmlns:c="{C_NS}"><c:numRef><c:numCache>{npts}</c:numCache>'
                f"</c:numRef></c:val>"
            )
        series_xml += (
            f'<c:ser xmlns:c="{C_NS}">'
            f'<c:tx><c:strRef><c:strCache>'
            f'<c:pt xmlns:c="{C_NS}" idx="0"><c:v>{s_name}</c:v></c:pt>'
            f"</c:strCache></c:strRef></c:tx>"
            f"{cat_xml}{val_xml}"
            "</c:ser>"
        )

    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<c:chartSpace xmlns:c="{C_NS}">'
        f'<c:chart>{title_xml}<c:plotArea>{series_xml}</c:plotArea></c:chart>'
        "</c:chartSpace>"
    )


def _pptx(tmp: Path, name: str, parts: dict) -> Path:
    """Write a zip with [Content_Types].xml + the given parts dict."""
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", PPTX_CT)
        for member, data in parts.items():
            z.writestr(member, data)
    return p


def _fid(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _build(*paths):
    return (GraphBuilder().register(EX)
            .register_resolver(*resolvers.default_resolvers())
            .build_files(list(paths)))


def _one_slide_deck(tmp: Path, name: str = "deck.pptx", title=None,
                    body=None, core=False) -> Path:
    """A minimal single-slide deck, optionally with title and body text."""
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": _slide(title=title, body_paras=body),
    }
    if core:
        parts["docProps/core.xml"] = CORE_XML
    return _pptx(tmp, name, parts)


# --------------------------------------------------------------------------- #
# handles + identity
# --------------------------------------------------------------------------- #
def test_handles():
    assert EX.handles(Path("a/Deck.pptx")) is True
    assert EX.handles(Path("a/DECK.PPTX")) is True
    assert EX.handles(Path("a/Macro.pptm")) is True
    assert EX.handles(Path("a/Legacy.ppt")) is False     # legacy binary rejected
    assert EX.handles(Path("a/Spec.docx")) is False
    assert EX.handles(Path("a/Sheet.xlsx")) is False


def test_docfile_identity_hash_id_filename_label(tmp_path):
    p = _one_slide_deck(tmp_path, "Acme Deck.pptx", title="Overview")
    nodes, _ = EX.extract(p)
    fid = _fid(p)
    ids = {n["id"]: n for n in nodes}
    doc = ids[f"docfile/{fid}"]
    assert doc["type"] == "docfile" and doc["label"] == "Acme Deck.pptx"
    assert doc["source"] == "docs" and doc["doc_type"] == "pptx"


def test_same_content_different_name_same_node_id(tmp_path):
    a = _one_slide_deck(tmp_path, "a.pptx", title="Slide")
    b = _one_slide_deck(tmp_path / "sub", "renamed.pptx", title="Slide")
    na, _ = EX.extract(a)
    nb, _ = EX.extract(b)
    assert na[0]["id"] == nb[0]["id"]      # content identity
    assert na[0]["label"] != nb[0]["label"]  # filename stays visible


def test_pptm_handled_as_pptx(tmp_path):
    p = _one_slide_deck(tmp_path, "macro.pptm", title="Title")
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["doc_type"] == "pptx"       # same format family


# --------------------------------------------------------------------------- #
# slide order, titles, body text
# --------------------------------------------------------------------------- #
def test_slide_order_and_titles(tmp_path):
    """Slides are emitted in the order declared in p:sldIdLst, not zip order."""
    parts = {
        "ppt/presentation.xml": _presentation(["rId1", "rId2", "rId3"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels(
            "slides/slide1.xml", "slides/slide2.xml", "slides/slide3.xml"),
        "ppt/slides/slide1.xml": _slide(title="First"),
        "ppt/slides/slide2.xml": _slide(title="Second"),
        "ppt/slides/slide3.xml": _slide(title="Third"),
    }
    p = _pptx(tmp_path, "order.pptx", parts)
    nodes, _ = EX.extract(p)
    slides = [n for n in nodes if n["type"] == "slide"]
    assert len(slides) == 3
    by_ord = {n["ordinal"]: n for n in slides}
    assert by_ord[1]["label"] == "First"
    assert by_ord[2]["label"] == "Second"
    assert by_ord[3]["label"] == "Third"


def test_slide_without_title_gets_fallback_label(tmp_path):
    p = _one_slide_deck(tmp_path, "notitle.pptx")  # no title kwarg
    nodes, _ = EX.extract(p)
    slide = next(n for n in nodes if n["type"] == "slide")
    assert slide["label"] == "Slide 1"


def test_slide_body_text_captured(tmp_path):
    p = _one_slide_deck(tmp_path, "body.pptx",
                        title="Main",
                        body=["First bullet.", "Second bullet."])
    nodes, _ = EX.extract(p)
    slide = next(n for n in nodes if n["type"] == "slide")
    assert "First bullet." in slide["text"]
    assert "Second bullet." in slide["text"]


def test_title_text_not_duplicated_in_body(tmp_path):
    """The title placeholder text must NOT appear in slide['text']."""
    p = _one_slide_deck(tmp_path, "dup.pptx", title="MyTitle",
                        body=["Body line."])
    nodes, _ = EX.extract(p)
    slide = next(n for n in nodes if n["type"] == "slide")
    assert slide["label"] == "MyTitle"
    # title run must not also appear in the body text attr
    assert "MyTitle" not in slide.get("text", "")
    assert "Body line." in slide["text"]


def test_slide_count_on_docfile(tmp_path):
    parts = {
        "ppt/presentation.xml": _presentation(["rId1", "rId2"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels(
            "slides/slide1.xml", "slides/slide2.xml"),
        "ppt/slides/slide1.xml": _slide(title="A"),
        "ppt/slides/slide2.xml": _slide(title="B"),
    }
    p = _pptx(tmp_path, "two.pptx", parts)
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["slide_count"] == 2


# --------------------------------------------------------------------------- #
# speaker notes
# --------------------------------------------------------------------------- #
def test_speaker_notes_captured_on_slide(tmp_path):
    slide_xml = _slide(title="Overview")
    slide_rels_xml = _slide_rels(
        ("rId10", "notesSlide", "../noteSlides/notesSlide1.xml"))
    notes_xml = _notes_slide("Remember to mention the ACME telemetry pipeline.")
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": slide_xml,
        "ppt/slides/_rels/slide1.xml.rels": slide_rels_xml,
        "ppt/noteSlides/notesSlide1.xml": notes_xml,
    }
    p = _pptx(tmp_path, "notes.pptx", parts)
    nodes, _ = EX.extract(p)
    slide = next(n for n in nodes if n["type"] == "slide")
    assert "ACME telemetry pipeline" in slide.get("notes", "")


def test_slide_without_notes_has_no_notes_attr(tmp_path):
    p = _one_slide_deck(tmp_path, "nonotes.pptx", title="No Notes")
    nodes, _ = EX.extract(p)
    slide = next(n for n in nodes if n["type"] == "slide")
    assert "notes" not in slide


# --------------------------------------------------------------------------- #
# tables — first row = columns, data rows excluded
# --------------------------------------------------------------------------- #
def test_table_first_row_columns_on_slide(tmp_path):
    p = _one_slide_deck(tmp_path, "tbl.pptx",
                        title="Mapping",
                        body=None)
    # re-build with a table
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": _slide(
            title="Mapping",
            table_rows=[
                ["Pole Acme", "Pole SAP"],    # header row
                ["MeterPoint__c", "EQUI"],    # data row — must NOT appear
            ],
        ),
    }
    p = _pptx(tmp_path, "tbl.pptx", parts)
    nodes, _ = EX.extract(p)
    slide = next(n for n in nodes if n["type"] == "slide")
    assert slide.get("columns") == ["Pole Acme", "Pole SAP"]
    dump = json.dumps(nodes, ensure_ascii=False)
    assert "MeterPoint__c" not in dump   # data row value not captured
    assert "EQUI" not in dump


def test_slide_with_no_table_has_no_columns(tmp_path):
    p = _one_slide_deck(tmp_path, "notbl.pptx", title="Flat", body=["Some text."])
    nodes, _ = EX.extract(p)
    slide = next(n for n in nodes if n["type"] == "slide")
    assert "columns" not in slide


# --------------------------------------------------------------------------- #
# charts — title + series + categories captured; numeric values absent
# --------------------------------------------------------------------------- #
def test_chart_series_categories_title_captured(tmp_path):
    chart_xml = _chart_xml(
        title="Pipeline Volumes",
        series=["Inbound", "Outbound"],
        categories=["Jan", "Feb", "Mar"],
        numeric_vals=[100, 200, 150],   # MUST NOT appear anywhere in output
    )
    slide_rels_xml = _slide_rels(("rId20", "chart", "../charts/chart1.xml"))
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": _slide(title="Results"),
        "ppt/slides/_rels/slide1.xml.rels": slide_rels_xml,
        "ppt/charts/chart1.xml": chart_xml,
    }
    p = _pptx(tmp_path, "chart.pptx", parts)
    nodes, _ = EX.extract(p)

    charts = [n for n in nodes if n["type"] == "chart"]
    assert len(charts) == 1
    ch = charts[0]
    assert ch["label"] == "Pipeline Volumes"
    assert ch["series"] == ["Inbound", "Outbound"]
    assert ch["categories"] == ["Jan", "Feb", "Mar"]

    # numeric values must not appear anywhere in any node attribute
    dump = json.dumps(nodes, ensure_ascii=False)
    assert "100" not in dump
    assert "200" not in dump
    assert "150" not in dump


def test_chart_wired_to_slide(tmp_path):
    chart_xml = _chart_xml(title="Traffic", series=["Requests"])
    slide_rels_xml = _slide_rels(("rId20", "chart", "../charts/chart1.xml"))
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": _slide(title="Slide One"),
        "ppt/slides/_rels/slide1.xml.rels": slide_rels_xml,
        "ppt/charts/chart1.xml": chart_xml,
    }
    p = _pptx(tmp_path, "ch_wire.pptx", parts)
    fid = _fid(p)
    nodes, edges = EX.extract(p)
    slide = next(n for n in nodes if n["type"] == "slide")
    chart = next(n for n in nodes if n["type"] == "chart")
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert (slide["id"], "contains", "chart", f"{fid}#1") in et


def test_chart_without_title_gets_fallback_label(tmp_path):
    chart_xml = _chart_xml(series=["Series A"])  # no title
    slide_rels_xml = _slide_rels(("rId20", "chart", "../charts/chart1.xml"))
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": _slide(title="Slide"),
        "ppt/slides/_rels/slide1.xml.rels": slide_rels_xml,
        "ppt/charts/chart1.xml": chart_xml,
    }
    p = _pptx(tmp_path, "notitlechart.pptx", parts)
    nodes, _ = EX.extract(p)
    chart = next((n for n in nodes if n["type"] == "chart"), None)
    assert chart is not None and chart["label"] == "Chart 1"


def test_chart_with_no_text_yields_no_chart_node(tmp_path):
    """A chart XML with no title, no series names, no categories → no chart node."""
    empty_chart = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<c:chartSpace xmlns:c="{C_NS}">'
        f'<c:chart><c:plotArea></c:plotArea></c:chart>'
        "</c:chartSpace>"
    )
    slide_rels_xml = _slide_rels(("rId20", "chart", "../charts/chart1.xml"))
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": _slide(title="Slide"),
        "ppt/slides/_rels/slide1.xml.rels": slide_rels_xml,
        "ppt/charts/chart1.xml": empty_chart,
    }
    p = _pptx(tmp_path, "emptychart.pptx", parts)
    nodes, _ = EX.extract(p)
    assert not any(n["type"] == "chart" for n in nodes)


# --------------------------------------------------------------------------- #
# sections — declared tier + docsection wiring
# --------------------------------------------------------------------------- #
def _sectioned_deck(tmp_path, name="sections.pptx"):
    """Three-slide deck with two sections: section A owns slides 1+2, section B owns slide 3."""
    parts = {
        "ppt/presentation.xml": _presentation(
            ["rId1", "rId2", "rId3"],
            sections=[
                ("Section A", ["rId1", "rId2"]),
                ("Section B", ["rId3"]),
            ],
        ),
        "ppt/_rels/presentation.xml.rels": _prs_rels(
            "slides/slide1.xml", "slides/slide2.xml", "slides/slide3.xml"),
        "ppt/slides/slide1.xml": _slide(title="Intro"),
        "ppt/slides/slide2.xml": _slide(title="Details"),
        "ppt/slides/slide3.xml": _slide(title="Summary"),
    }
    return _pptx(tmp_path, name, parts)


def test_sections_declared_tier_and_docsection_nodes(tmp_path):
    p = _sectioned_deck(tmp_path)
    nodes, edges = EX.extract(p)
    fid = _fid(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["structure"] == "declared"

    sections = [n for n in nodes if n["type"] == "docsection"]
    assert len(sections) == 2
    sec_by_label = {n["label"]: n for n in sections}
    assert set(sec_by_label) == {"Section A", "Section B"}
    # sections carry no confidence attr (declared is the trusted default)
    for sec in sections:
        assert "confidence" not in sec

    # docfile → docsection edges
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert (f"docfile/{fid}", "contains", "docsection", f"{fid}#s1") in et
    assert (f"docfile/{fid}", "contains", "docsection", f"{fid}#s2") in et


def test_sections_wire_slides_to_docsection(tmp_path):
    p = _sectioned_deck(tmp_path)
    fid = _fid(p)
    nodes, edges = EX.extract(p)
    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    # slides 1+2 under Section A (ordinal 1)
    assert (f"docsection/{fid}#s1", "contains", "slide", f"{fid}#1") in et
    assert (f"docsection/{fid}#s1", "contains", "slide", f"{fid}#2") in et
    # slide 3 under Section B (ordinal 2)
    assert (f"docsection/{fid}#s2", "contains", "slide", f"{fid}#3") in et


# --------------------------------------------------------------------------- #
# sectionless decks — structure "none", slides directly under docfile
# --------------------------------------------------------------------------- #
def test_sectionless_structure_none_flat_wiring(tmp_path):
    """No sections → structure "none"; each slide is directly under docfile."""
    parts = {
        "ppt/presentation.xml": _presentation(["rId1", "rId2"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels(
            "slides/slide1.xml", "slides/slide2.xml"),
        "ppt/slides/slide1.xml": _slide(title="Alpha"),
        "ppt/slides/slide2.xml": _slide(title="Beta"),
    }
    p = _pptx(tmp_path, "nosec.pptx", parts)
    fid = _fid(p)
    nodes, edges = EX.extract(p)

    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["structure"] == "none"
    assert not any(n["type"] == "docsection" for n in nodes)

    et = {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}
    assert (f"docfile/{fid}", "contains", "slide", f"{fid}#1") in et
    assert (f"docfile/{fid}", "contains", "slide", f"{fid}#2") in et


# --------------------------------------------------------------------------- #
# detect_refs
# --------------------------------------------------------------------------- #
def test_detect_refs_jira_key_and_sf_name(tmp_path):
    """ACME-1234 jira key and Foo__c sf name in slide text appear as docfile attrs."""
    p = _one_slide_deck(tmp_path, "refs.pptx",
                        title="Integration Spec",
                        body=["See ACME-1234 for context; field Foo__c required."])
    nodes, edges = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert "ACME-1234" in doc.get("jira_keys", [])
    assert "Foo__c" in doc.get("sf_names", [])
    # refs are attrs on the docfile, never edges
    assert not any(e["to_kind"] in ("jiraissue", "object") for e in edges)


def test_detect_refs_in_title_and_notes(tmp_path):
    """Refs found in slide title and speaker notes also surface on docfile."""
    slide_rels_xml = _slide_rels(
        ("rId10", "notesSlide", "../noteSlides/notesSlide1.xml"))
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": _slide(title="ACME-99 overview"),
        "ppt/slides/_rels/slide1.xml.rels": slide_rels_xml,
        "ppt/noteSlides/notesSlide1.xml": _notes_slide("Check ACME-77 and Bar__c."),
    }
    p = _pptx(tmp_path, "refsall.pptx", parts)
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert "ACME-99" in doc.get("jira_keys", [])
    assert "ACME-77" in doc.get("jira_keys", [])
    assert "Bar__c" in doc.get("sf_names", [])


# --------------------------------------------------------------------------- #
# core props
# --------------------------------------------------------------------------- #
def test_core_props_title_and_modified_but_never_author(tmp_path):
    p = _one_slide_deck(tmp_path, "core.pptx", title="Slide1", core=True)
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["title"] == "Acme Integration Overview"
    assert doc["modified"] == "2026-05-15T09:00:00Z"
    assert "Acme Author" not in json.dumps(nodes)


# --------------------------------------------------------------------------- #
# image-only slide — slide node exists, no text, nothing image-related
# --------------------------------------------------------------------------- #
def test_image_only_slide_yields_slide_node_no_text(tmp_path):
    """A slide with only an image (blipFill / pic) emits a slide node with no
    text, notes or columns attrs, and nothing image-related appears anywhere."""
    image_slide = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{RNS}">'
        f'<p:cSld><p:spTree>'
        # a pic shape with a blipFill — images are ignored
        f'<p:pic><p:nvPicPr/>'
        f'<p:blipFill><a:blip r:embed="rId1"/></p:blipFill>'
        f"</p:pic>"
        f"</p:spTree></p:cSld>"
        "</p:sld>"
    )
    parts = {
        "ppt/presentation.xml": _presentation(["rId1"]),
        "ppt/_rels/presentation.xml.rels": _prs_rels("slides/slide1.xml"),
        "ppt/slides/slide1.xml": image_slide,
    }
    p = _pptx(tmp_path, "img.pptx", parts)
    nodes, _ = EX.extract(p)
    slides = [n for n in nodes if n["type"] == "slide"]
    assert len(slides) == 1
    sl = slides[0]
    assert "text" not in sl
    assert "notes" not in sl
    assert "columns" not in sl
    dump = json.dumps(nodes, ensure_ascii=False)
    # nothing image-related in any node attr
    assert "blip" not in dump
    assert "rId1" not in dump


# --------------------------------------------------------------------------- #
# corrupt files → build errors; full-build resolution; determinism
# --------------------------------------------------------------------------- #
def test_broken_zip_recorded_in_errors(tmp_path):
    p = tmp_path / "broken.pptx"
    p.write_bytes(b"this is not a zip archive at all")
    g = _build(p)
    assert g["nodes"] == [] and len(g["errors"]) == 1
    assert g["errors"][0]["source"] == "docs"
    assert g["errors"][0]["path"] == "broken.pptx"


def test_broken_inner_xml_recorded_in_errors(tmp_path):
    p = tmp_path / "badxml.pptx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", PPTX_CT)
        z.writestr("ppt/presentation.xml", "<p:presentation <<< not xml")
    g = _build(p)
    assert g["nodes"] == [] and len(g["errors"]) == 1


def test_full_build_resolves_all_edges_no_stubs(tmp_path):
    p = _sectioned_deck(tmp_path, "build.pptx")
    g = _build(p)
    fid = _fid(p)
    assert g["errors"] == [] and g["unresolved"] == []
    assert not any(n.get("external") for n in g["nodes"])
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert (f"docfile/{fid}", "contains", f"docsection/{fid}#s1") in edges
    assert (f"docsection/{fid}#s1", "contains", f"slide/{fid}#1") in edges
    assert all(n.get("source_path") for n in g["nodes"])


def test_double_build_is_identical(tmp_path):
    p = _sectioned_deck(tmp_path, "det.pptx")
    assert EX.extract(p) == EX.extract(p)
