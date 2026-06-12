"""Xlsx extractor tests — extract() + full builds over in-test-built fixtures.

Fixtures are minimal OOXML zips authored with stdlib ``zipfile`` (fictional
Acme / MeterPoint content, Polish strings for unicode coverage). They cover the
tiers (T1 declared Table parts, the gated T2 first-row header on table-less
sheets, T3 dimensions-only), the confidentiality rules (no cell values, no
formula bodies, no macro content, no authors) and corrupt files -> ``errors``.
"""
import hashlib
import json
import zipfile
from pathlib import Path

import graphbuilder.resolvers as resolvers
from graphbuilder.core import GraphBuilder
from graphbuilder.extractors.xlsx import XlsxExtractor

EX = XlsxExtractor()

SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PK = "http://schemas.openxmlformats.org/package/2006/relationships"

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType='
    '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    "</Types>"
)


def _workbook(sheet_names, defined=()):
    sheets = "".join(
        f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, n in enumerate(sheet_names))
    dn = ""
    if defined:
        dn = ("<definedNames>" + "".join(
            f'<definedName name="{n}">Dane!$A$1:$C$5</definedName>' for n in defined)
            + "</definedNames>")
    return (f'<workbook xmlns="{SS}" xmlns:r="{RNS}">'
            f"<sheets>{sheets}</sheets>{dn}</workbook>")


def _wb_rels(n):
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{RNS}/worksheet"'
        f' Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
    return f'<Relationships xmlns="{PK}">{rels}</Relationships>'


def _sst(strings):
    body = "".join(f"<si><t>{s}</t></si>" for s in strings)
    return f'<sst xmlns="{SS}" count="{len(strings)}" uniqueCount="{len(strings)}">{body}</sst>'


def _c(ref, t=None, v=None, inline=None, formula=None):
    ta = f' t="{t}"' if t else ""
    body = ""
    if formula is not None:
        body += f"<f>{formula}</f>"
    if v is not None:
        body += f"<v>{v}</v>"
    if inline is not None:
        body = f"<is><t>{inline}</t></is>"
    return f'<c r="{ref}"{ta}>{body}</c>'


def _row(r, *cells):
    return f'<row r="{r}">{"".join(cells)}</row>'


def _ws(*rows, dimension=None, frozen=None):
    dim = f'<dimension ref="{dimension}"/>' if dimension else ""
    views = ""
    if frozen:
        views = ('<sheetViews><sheetView workbookViewId="0">'
                 f'<pane ySplit="{frozen}" topLeftCell="A{frozen + 1}" state="frozen"/>'
                 "</sheetView></sheetViews>")
    return (f'<worksheet xmlns="{SS}" xmlns:r="{RNS}">{dim}{views}'
            f'<sheetData>{"".join(rows)}</sheetData></worksheet>')


def _table(name, columns, ref="A1:C3"):
    cols = "".join(
        f'<tableColumn id="{i + 1}" name="{c}"/>' for i, c in enumerate(columns))
    return (f'<table xmlns="{SS}" id="1" name="{name}" displayName="{name}" ref="{ref}">'
            f'<tableColumns count="{len(columns)}">{cols}</tableColumns></table>')


def _sheet_rels(*rels):
    body = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{RNS}/{rtype}" Target="{target}"'
        + (' TargetMode="External"' if external else "") + "/>"
        for i, (rtype, target, external) in enumerate(rels))
    return f'<Relationships xmlns="{PK}">{body}</Relationships>'


def _xlsx(tmp: Path, name: str, parts: dict) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        for member, data in parts.items():
            z.writestr(member, data)
    return p


def _fid(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _build(*paths):
    return (GraphBuilder().register(EX)
            .register_resolver(*resolvers.default_resolvers())
            .build_files(list(paths)))


# one plain sheet whose first row is a string header and whose second row has a
# numeric cell (the type-contrast signal); Polish headers for unicode coverage
HEADER_SST = _sst(["Punkt pomiarowy", "Wartość nominalna", "MP-A1"])
HEADER_WS = _ws(
    _row(1, _c("A1", t="s", v=0), _c("B1", t="s", v=1)),
    _row(2, _c("A2", t="s", v=2), _c("B2", v="42.5")),
    dimension="A1:B2",
)


def _plain(tmp, name="dane.xlsx", ws=HEADER_WS, sst=HEADER_SST, sheet="Dane", defined=()):
    return _xlsx(tmp, name, {
        "xl/workbook.xml": _workbook([sheet], defined=defined),
        "xl/_rels/workbook.xml.rels": _wb_rels(1),
        "xl/worksheets/sheet1.xml": ws,
        "xl/sharedStrings.xml": sst,
    })


# --------------------------------------------------------------------------- #
# handles + identity
# --------------------------------------------------------------------------- #
def test_handles():
    assert EX.handles(Path("a/Mapping.xlsx")) is True
    assert EX.handles(Path("a/Mapping.XLSM")) is True     # macro-enabled accepted
    assert EX.handles(Path("a/Mapping.xlsb")) is False    # binary format rejected
    assert EX.handles(Path("a/Mapping.xls")) is False     # legacy binary rejected
    assert EX.handles(Path("a/Spec.docx")) is False


def test_docfile_and_sheet_nodes(tmp_path):
    p = _plain(tmp_path)
    nodes, edges = EX.extract(p)
    fid = _fid(p)
    ids = {n["id"]: n for n in nodes}
    doc = ids[f"docfile/{fid}"]
    assert doc["type"] == "docfile" and doc["label"] == "dane.xlsx"
    assert doc["source"] == "docs" and doc["doc_type"] == "xlsx"
    assert doc["sheet_count"] == 1 and "has_macros" not in doc
    sheet = ids[f"sheet/{fid}#Dane"]
    assert sheet["label"] == "Dane"
    assert sheet["row_count"] == 2 and sheet["col_count"] == 2   # from the dimension
    assert {(e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges} >= {
        (f"docfile/{fid}", "contains", "sheet", f"{fid}#Dane")}


# --------------------------------------------------------------------------- #
# T1 — declared Table parts
# --------------------------------------------------------------------------- #
def _with_table(tmp, name="meters.xlsx"):
    return _xlsx(tmp, name, {
        "xl/workbook.xml": _workbook(["Punkt pomiarowy"]),
        "xl/_rels/workbook.xml.rels": _wb_rels(1),
        "xl/worksheets/sheet1.xml": HEADER_WS,
        "xl/worksheets/_rels/sheet1.xml.rels": _sheet_rels(
            ("table", "../tables/table1.xml", False)),
        "xl/tables/table1.xml": _table("Mierniki", ["Punkt pomiarowy", "Wartość nominalna"]),
        "xl/sharedStrings.xml": HEADER_SST,
    })


def test_t1_declared_table_node_with_declared_columns(tmp_path):
    p = _with_table(tmp_path)
    nodes, edges = EX.extract(p)
    fid = _fid(p)
    ids = {n["id"]: n for n in nodes}
    doc = ids[f"docfile/{fid}"]
    assert doc["structure"] == "declared"
    table = ids[f"datatable/{fid}#Mierniki"]
    assert table["label"] == "Mierniki"
    assert table["columns"] == ["Punkt pomiarowy", "Wartość nominalna"]
    assert "confidence" not in table                      # declared = trusted default
    assert (f"sheet/{fid}#Punkt pomiarowy", "contains", "datatable", f"{fid}#Mierniki") in {
        (e["src"], e["type"], e["to_kind"], e["to_name"]) for e in edges}


def test_t1_sheet_with_table_gets_no_heuristic_columns(tmp_path):
    p = _with_table(tmp_path)
    nodes, _ = EX.extract(p)
    sheet = next(n for n in nodes if n["type"] == "sheet")
    # the declared table IS the structure; the header guess would only shadow it
    assert "columns" not in sheet and "confidence" not in sheet


# --------------------------------------------------------------------------- #
# T2 — gated first-row header (table-less sheets only)
# --------------------------------------------------------------------------- #
def test_t2_type_contrast_accepts_header(tmp_path):
    p = _plain(tmp_path)
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    sheet = next(n for n in nodes if n["type"] == "sheet")
    assert doc["structure"] == "heuristic"
    assert sheet["columns"] == ["Punkt pomiarowy", "Wartość nominalna"]
    assert sheet["confidence"] == "heuristic"             # a guess is marked as one
    # NO cell values may enter the graph — not the numeric, not the string one
    dump = json.dumps(nodes, ensure_ascii=False)
    assert "42.5" not in dump and "MP-A1" not in dump


def test_t2_freeze_pane_accepts_header_without_contrast(tmp_path):
    all_string = _ws(
        _row(1, _c("A1", t="s", v=0), _c("B1", t="s", v=1)),
        _row(2, _c("A2", t="s", v=2), _c("B2", t="s", v=2)),
        dimension="A1:B2", frozen=1,
    )
    nodes, _ = EX.extract(_plain(tmp_path, ws=all_string))
    sheet = next(n for n in nodes if n["type"] == "sheet")
    assert sheet["columns"] == ["Punkt pomiarowy", "Wartość nominalna"]


def test_t3_no_freeze_no_contrast_dimensions_only(tmp_path):
    all_string = _ws(
        _row(1, _c("A1", t="s", v=0), _c("B1", t="s", v=1)),
        _row(2, _c("A2", t="s", v=2), _c("B2", t="s", v=2)),
        dimension="A1:B2",
    )
    nodes, _ = EX.extract(_plain(tmp_path, ws=all_string))
    doc = next(n for n in nodes if n["type"] == "docfile")
    sheet = next(n for n in nodes if n["type"] == "sheet")
    assert doc["structure"] == "none"                     # honest flat
    assert "columns" not in sheet
    assert sheet["row_count"] == 2 and sheet["col_count"] == 2


def test_t2_rejects_numeric_duplicate_or_empty_headers(tmp_path):
    numeric_first = _ws(
        _row(1, _c("A1", v="2026"), _c("B1", t="s", v=0)),     # numeric "header" cell
        _row(2, _c("A2", v="1"), _c("B2", v="2")),
        dimension="A1:B2",
    )
    dup = _ws(
        _row(1, _c("A1", t="s", v=0), _c("B1", t="s", v=0)),   # duplicate names
        _row(2, _c("A2", v="1"), _c("B2", v="2")),
        dimension="A1:B2",
    )
    for ws in (numeric_first, dup):
        nodes, _ = EX.extract(_plain(tmp_path, name=f"r{len(ws)}.xlsx", ws=ws))
        sheet = next(n for n in nodes if n["type"] == "sheet")
        assert "columns" not in sheet
        # the rejected numeric "header" value must not leak anywhere
        assert "2026" not in json.dumps(nodes)


def test_t2_inline_string_header_accepted(tmp_path):
    ws = _ws(
        _row(1, _c("A1", t="inlineStr", inline="Miernik"), _c("B1", t="inlineStr", inline="Odczyt")),
        _row(2, _c("A2", v="7"), _c("B2", v="8")),
        dimension="A1:B2",
    )
    nodes, _ = EX.extract(_plain(tmp_path, ws=ws, sst=_sst([])))
    sheet = next(n for n in nodes if n["type"] == "sheet")
    assert sheet["columns"] == ["Miernik", "Odczyt"]


def test_mixed_workbook_per_sheet_tiers(tmp_path):
    """A declared table on one sheet must not suppress the (gated) header
    heuristic on another, independent grid — tier isolation is per text flow,
    and each sheet is its own."""
    p = _xlsx(tmp_path, "mixed.xlsx", {
        "xl/workbook.xml": _workbook(["Mierniki", "Dane"]),
        "xl/_rels/workbook.xml.rels": _wb_rels(2),
        "xl/worksheets/sheet1.xml": _ws(dimension="A1:C3"),
        "xl/worksheets/_rels/sheet1.xml.rels": _sheet_rels(
            ("table", "../tables/table1.xml", False)),
        "xl/tables/table1.xml": _table("Mierniki", ["Meter ID", "Status"]),
        "xl/worksheets/sheet2.xml": HEADER_WS,
        "xl/sharedStrings.xml": HEADER_SST,
    })
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    sheets = {n["label"]: n for n in nodes if n["type"] == "sheet"}
    assert doc["structure"] == "declared"                 # T1 anywhere wins the file tier
    assert sheets["Dane"]["columns"] == ["Punkt pomiarowy", "Wartość nominalna"]
    assert sheets["Dane"]["confidence"] == "heuristic"
    assert "columns" not in sheets["Mierniki"]            # its table carries the structure


# --------------------------------------------------------------------------- #
# confidentiality — values, formulas, macros, authors stay out
# --------------------------------------------------------------------------- #
def test_formula_bodies_never_captured(tmp_path):
    ws = _ws(
        _row(1, _c("A1", t="s", v=0), _c("B1", t="s", v=1)),
        _row(2, _c("A2", v="5"), _c("B2", t="str", v="SECRET RESULT",
                                    formula='WEBSERVICE("https://internal.acme.invalid")')),
        dimension="A1:B2",
    )
    nodes, _ = EX.extract(_plain(tmp_path, ws=ws))
    dump = json.dumps(nodes, ensure_ascii=False)
    assert "WEBSERVICE" not in dump and "internal.acme.invalid" not in dump
    assert "SECRET RESULT" not in dump                    # formula results are values too


def test_defined_names_only_never_refers_to(tmp_path):
    p = _plain(tmp_path, defined=("ZakresPomiarow", "_xlnm.Print_Area"))
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["defined_names"] == ["ZakresPomiarow"]     # _xlnm built-ins are noise
    assert "$A$1" not in json.dumps(nodes)                # the range stays behind


def test_xlsm_flags_macros_but_never_reads_them(tmp_path):
    p = _xlsx(tmp_path, "makro.xlsm", {
        "xl/workbook.xml": _workbook(["Dane"]),
        "xl/_rels/workbook.xml.rels": _wb_rels(1),
        "xl/worksheets/sheet1.xml": HEADER_WS,
        "xl/sharedStrings.xml": HEADER_SST,
        "xl/vbaProject.bin": b"MACRO_PAYLOAD Sub Evil()",
    })
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["has_macros"] is True and doc["doc_type"] == "xlsx"   # same format family
    assert "MACRO_PAYLOAD" not in json.dumps(nodes)


def test_core_props_title_modified_but_never_authors(tmp_path):
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties'
            ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
            ' xmlns:dcterms="http://purl.org/dc/terms/">'
            "<dc:title>Mapowanie punktów pomiarowych</dc:title>"
            "<dc:creator>Acme Author</dc:creator>"
            "<dcterms:modified>2026-04-02T08:00:00Z</dcterms:modified>"
            "</cp:coreProperties>")
    p = _xlsx(tmp_path, "core.xlsx", {
        "xl/workbook.xml": _workbook(["Dane"]),
        "xl/_rels/workbook.xml.rels": _wb_rels(1),
        "xl/worksheets/sheet1.xml": _ws(dimension="A1:B2"),
        "docProps/core.xml": core,
    })
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["title"] == "Mapowanie punktów pomiarowych"
    assert doc["modified"] == "2026-04-02T08:00:00Z"
    assert "Acme Author" not in json.dumps(nodes)


# --------------------------------------------------------------------------- #
# detected refs, hyperlinks
# --------------------------------------------------------------------------- #
def test_refs_detected_in_captured_names_only(tmp_path):
    sst = _sst(["MeterPoint__c", "Status ACME-9", "cell-only CELL-77 never scanned"])
    ws = _ws(
        _row(1, _c("A1", t="s", v=0), _c("B1", t="s", v=1)),
        _row(2, _c("A2", t="s", v=2), _c("B2", v="1")),
        dimension="A1:B2",
    )
    p = _plain(tmp_path, ws=ws, sst=sst, sheet="ACME-12 mapping")
    nodes, edges = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    # sheet name + accepted header names are captured -> scanned for refs
    assert doc["jira_keys"] == ["ACME-12", "ACME-9"]
    assert doc["sf_names"] == ["MeterPoint__c"]
    # the non-header cell value is NOT captured, so its ref is not scanned either
    assert "CELL-77" not in json.dumps(nodes)
    assert {e["to_kind"] for e in edges} <= {"sheet", "datatable"}   # attrs, never edges


def test_hyperlink_rels_become_urls_attr(tmp_path):
    p = _xlsx(tmp_path, "links.xlsx", {
        "xl/workbook.xml": _workbook(["Dane"]),
        "xl/_rels/workbook.xml.rels": _wb_rels(1),
        "xl/worksheets/sheet1.xml": _ws(dimension="A1:B2"),
        "xl/worksheets/_rels/sheet1.xml.rels": _sheet_rels(
            ("hyperlink", "https://wiki.example.invalid/spec", True)),
    })
    nodes, _ = EX.extract(p)
    doc = next(n for n in nodes if n["type"] == "docfile")
    assert doc["urls"] == ["https://wiki.example.invalid/spec"]


# --------------------------------------------------------------------------- #
# corrupt files -> build errors; full-build resolution; determinism
# --------------------------------------------------------------------------- #
def test_broken_zip_recorded_in_errors(tmp_path):
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"not a zip archive")
    g = _build(p)
    assert g["nodes"] == [] and len(g["errors"]) == 1
    assert g["errors"][0]["source"] == "docs" and g["errors"][0]["path"] == "broken.xlsx"


def test_missing_workbook_part_recorded_in_errors(tmp_path):
    p = tmp_path / "noworkbook.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
    g = _build(p)
    assert g["nodes"] == [] and len(g["errors"]) == 1


def test_full_build_resolves_all_edges_no_stubs(tmp_path):
    p = _with_table(tmp_path)
    g = _build(p)
    fid = _fid(p)
    assert g["errors"] == [] and g["unresolved"] == []
    assert not any(n.get("external") for n in g["nodes"])
    edges = {(e["src"], e["type"], e["dst"]) for e in g["edges"]}
    assert (f"docfile/{fid}", "contains", f"sheet/{fid}#Punkt pomiarowy") in edges
    assert (f"sheet/{fid}#Punkt pomiarowy", "contains", f"datatable/{fid}#Mierniki") in edges
    assert all(n.get("source_path") for n in g["nodes"])


def test_double_build_is_identical(tmp_path):
    p = _with_table(tmp_path)
    assert EX.extract(p) == EX.extract(p)                 # same file -> identical graph


def test_same_content_different_name_same_node_id(tmp_path):
    a = _plain(tmp_path, name="a.xlsx")
    b = _plain(tmp_path / "sub", name="renamed.xlsx")
    na, _ = EX.extract(a)
    nb, _ = EX.extract(b)
    assert na[0]["id"] == nb[0]["id"]                     # content-keyed identity
    assert na[0]["label"] != nb[0]["label"]
