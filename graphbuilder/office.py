"""Office-document parsers — turn ``.docx`` / ``.xlsx`` files into typed dataclasses.

``docs`` is its own SEPARATE source (the fifth, after Salesforce / Confluence /
Jira / MuleSoft): stdlib-only ``zipfile`` + ``xml.etree`` over the OOXML parts,
with the parser -> dataclass split mirroring :mod:`graphbuilder.salesforce`. The
extractors (``extractors/docx.py``, ``extractors/xlsx.py``) turn these shapes
into nodes/edges.

Structure detection is TIERED — heterogeneous real-world documents must never be
guessed at uniformly:

  T1 DECLARED   (trusted; the default)  Word ``w:pStyle`` Heading1-9 / Title and
      an explicit ``w:outlineLvl``; Excel Table parts (``xl/tables/*.xml`` — the
      header row is *declared* there, not guessed).
  T2 HEURISTIC  (emitted with ``confidence: "heuristic"``, mirroring the joins'
      via/confidence)  Word bold-short-paragraph sections, applied ONLY when the
      document declares zero T1 headings (tiers never mix in one text flow);
      Excel first-row-as-header on sheets with no declared table, accepted only
      under the gate documented at the heuristic itself.
  T3 NONE       (honest flat)  no detectable structure -> the ``docfile`` node
      alone, ``structure: "none"``; sections are NEVER fabricated.

Confidentiality (hard rules, sharpening the engine-wide names-only policy):
NO cell values, NO formula bodies, NO author names. What may enter the graph:
names/labels/headers (sheet, table, column, defined names, heading titles) and —
the one deliberate content capture, like Confluence page bodies — Word section
text. References detected in that text (Jira keys, ``X__c`` API names, URLs)
become ATTRS only, never edges (domain isolation, same as Confluence
``jira_keys``).

A corrupt zip or malformed XML RAISES out of these parsers — the core records
the file in ``errors``, so one bad file never kills a build.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .xmlutil import child, local_name


# --------------------------------------------------------------------------- #
# shared helpers — file identity, detected refs, namespace-agnostic XML access
# --------------------------------------------------------------------------- #
def file_id(data: bytes) -> str:
    """First 12 hex chars of the file bytes' SHA-1 — the ``docfile/<id>`` name
    segment (and the prefix of every section/sheet/table id inside the file).
    Content-keyed identity makes a rename a non-event and dedup of an identical
    copy natural; the filename stays visible as the node label + source_path."""
    return hashlib.sha1(data).hexdigest()[:12]


_JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_SF_NAME = re.compile(r"\b\w+__c\b")
_URL = re.compile(r"https?://[^\s<>\"']+")


def detect_refs(text: str) -> dict:
    """Cross-source references DETECTED in text/names — Jira issue keys, custom
    Salesforce API names (``X__c``) and plain URLs. They surface as node ATTRS
    only, never edges: wiring ``docs`` to other sources would be a deliberate
    later join step, exactly like Confluence ``jira_keys``. Deduped, first-seen
    order; keys with no hits are absent (attrs only when non-empty)."""
    out: dict = {}
    keys = list(dict.fromkeys(m.group(0) for m in _JIRA_KEY.finditer(text or "")))
    if keys:
        out["jira_keys"] = keys
    names = list(dict.fromkeys(m.group(0) for m in _SF_NAME.finditer(text or "")))
    if names:
        out["sf_names"] = names
    urls = list(dict.fromkeys(
        u for u in (m.group(0).rstrip(".,;:!?)") for m in _URL.finditer(text or "")) if u))
    if urls:
        out["urls"] = urls
    return out


def _xml_root(data: bytes):
    """Parse one zip part, tolerant of junk before the XML declaration (a BOM,
    stray whitespace) like :func:`graphbuilder.xmlutil.parse_root`; genuinely
    malformed XML still raises so the build records the file in ``errors``.

    Stdlib ``xml.etree`` on purpose (the engine is dependency-free, like every
    other extractor): it does not fetch external entities (an undefined entity
    is a ``ParseError`` -> an ``errors`` entry), and an entity-expansion bomb
    in a local file aborts only that file's extraction, never the build."""
    import xml.etree.ElementTree as ET

    return ET.fromstring(data.lstrip(b"\xef\xbb\xbf\xff\xfe\r\n\t "))


def _attr(el, name: str) -> str:
    """Attribute value matched by LOCAL name (``w:val`` / ``r:id`` / plain
    ``val`` all match ``val``) — the attribute-side twin of xmlutil's
    namespace-agnostic element helpers."""
    if el is None:
        return ""
    for k, v in el.attrib.items():
        if k.rsplit("}", 1)[-1] == name:
            return v
    return ""


def _external_link_targets(data: bytes) -> list:
    """``Target`` of every external hyperlink relationship in a ``.rels`` part
    (dedup, file order) — link URLs live in the rels, not the document body."""
    out = []
    for rel in _xml_root(data).iter():
        if local_name(rel.tag) != "Relationship":
            continue
        if not (rel.get("Type") or "").endswith("/hyperlink"):
            continue
        if rel.get("TargetMode") != "External":
            continue
        target = (rel.get("Target") or "").strip()
        if target:
            out.append(target)
    return list(dict.fromkeys(out))


def _core_props(data: bytes, doc) -> None:
    """``dc:title`` + ``dcterms:modified`` from ``docProps/core.xml``.
    ``dc:creator`` / ``cp:lastModifiedBy`` are author NAMES — deliberately never
    read (anonymization by default)."""
    for el in _xml_root(data).iter():
        ln = local_name(el.tag)
        if ln == "title" and not doc.title:
            doc.title = (el.text or "").strip()
        elif ln == "modified":
            doc.modified = (el.text or "").strip()


# --------------------------------------------------------------------------- #
# Word (.docx) — parsed shapes
# --------------------------------------------------------------------------- #
@dataclass
class DocSection:
    title: str
    level: int = 1
    ordinal: int = 0       # 1-based document order — the `docsection/<id>#<n>` segment
    parent: int = 0        # owning section's ordinal; 0 = directly under the docfile
    confidence: str = ""   # "" = declared (T1, the trusted default) | "heuristic" (T2)
    text: str = ""         # body paragraphs up to the next same-or-higher-level heading
    columns: list = field(default_factory=list)   # first owned table's header row (names only)


@dataclass
class DocxDoc:
    file_id: str = ""
    structure: str = "none"    # declared | heuristic | none (the tier that produced sections)
    title: str = ""            # docProps dc:title (never the author)
    modified: str = ""         # docProps dcterms:modified (ISO string)
    sections: list = field(default_factory=list)   # list[DocSection], document order
    text: str = ""             # preamble before the first heading; the whole body when flat
    columns: list = field(default_factory=list)    # header of a table owned by no section
    urls: list = field(default_factory=list)       # hyperlink rels + URLs found in text
    jira_keys: list = field(default_factory=list)
    sf_names: list = field(default_factory=list)


# T2 bold-short-paragraph threshold: headings are short title lines (they fit
# well under a line of text), bolded prose runs longer and ends like a sentence.
# < 80 chars keeps real titles and rejects bolded sentences; a trailing period
# rejects bold emphasis that is still prose. Tuned ONCE from real-data evidence
# later (plan phase O5), not speculatively.
T2_MAX_CHARS = 80

_HEADING_STYLE = re.compile(r"(?i)^heading\s?([1-9])$")
_BOLD_OFF = ("0", "false", "off", "none")


def _para_text(p) -> str:
    """Joined text of every ``w:t`` under a paragraph (hyperlink runs included)."""
    return "".join((t.text or "") for t in p.iter() if local_name(t.tag) == "t").strip()


def _heading_level(p) -> int:
    """T1 mark: an explicit Heading1-9 / Title style, or an explicit
    ``w:outlineLvl`` (0-8 -> level 1-9). 0 = not a declared heading. Localized
    style ids that carry their outline level only in styles.xml are out of
    scope for v1 (they fall through to T2/T3 honestly)."""
    ppr = child(p, "pPr")
    if ppr is None:
        return 0
    style_val = _attr(child(ppr, "pStyle"), "val")
    m = _HEADING_STYLE.match(style_val)
    if m:
        return int(m.group(1))
    if style_val.lower() == "title":
        return 1
    lvl = child(ppr, "outlineLvl")
    if lvl is not None:
        try:
            v = int(_attr(lvl, "val"))
        except ValueError:
            return 0
        if 0 <= v <= 8:
            return v + 1
    return 0


def _is_bold(rpr, default: bool = False) -> bool:
    """OOXML on/off semantics for ``w:b``: present without a val = on; an
    explicit off value = off; absent = the caller's default (paragraph mark)."""
    if rpr is None:
        return default
    b = child(rpr, "b")
    if b is None:
        return default
    return _attr(b, "val").lower() not in _BOLD_OFF


def _bold_short(p, text: str) -> bool:
    """T2 candidate: a short (< :data:`T2_MAX_CHARS`), all-bold paragraph with
    no trailing period — the visual way ad-hoc documents fake headings. Every
    text-bearing run must be bold (run-level ``w:b``, falling back to the
    paragraph mark's)."""
    if not text or len(text) >= T2_MAX_CHARS or text.endswith("."):
        return False
    ppr = child(p, "pPr")
    para_bold = _is_bold(child(ppr, "rPr") if ppr is not None else None)
    saw_text = False
    for r in (el for el in p.iter() if local_name(el.tag) == "r"):
        if not "".join((t.text or "") for t in r.iter() if local_name(t.tag) == "t").strip():
            continue
        saw_text = True
        if not _is_bold(child(r, "rPr"), default=para_bold):
            return False
    return saw_text


def _table_columns(tbl) -> list:
    """First-row cell texts of a Word table — header NAMES only (the agreed
    capture; the table's data rows never enter the graph). Empty cells drop."""
    tr = next((el for el in tbl.iter() if local_name(el.tag) == "tr"), None)
    if tr is None:
        return []
    cols = []
    for tc in (c for c in tr if local_name(c.tag) == "tc"):
        txt = "".join((t.text or "") for t in tc.iter() if local_name(t.tag) == "t").strip()
        if txt:
            cols.append(txt)
    return cols


def parse_docx(path) -> DocxDoc:
    """Parse one ``.docx`` into a :class:`DocxDoc` (raises on a corrupt zip or
    malformed XML — the build records it in ``errors``)."""
    data = Path(path).read_bytes()
    doc = DocxDoc(file_id=file_id(data))
    rel_urls: list = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        root = _xml_root(zf.read("word/document.xml"))
        if "docProps/core.xml" in names:
            _core_props(zf.read("docProps/core.xml"), doc)
        if "word/_rels/document.xml.rels" in names:
            rel_urls = _external_link_targets(zf.read("word/_rels/document.xml.rels"))

    # one ordered pass over the body: paragraphs (text + T1 level + T2 flag)
    # and body-level tables (header row only)
    body = child(root, "body")
    blocks: list = []                       # ("p", text, t1_level, t2_flag) | ("tbl", columns, 0, False)
    for el in body if body is not None else ():
        ln = local_name(el.tag)
        if ln == "p":
            text = _para_text(el)
            blocks.append(("p", text, _heading_level(el), _bold_short(el, text)))
        elif ln == "tbl":
            blocks.append(("tbl", _table_columns(el), 0, False))

    # tier choice: declared headings win outright; the bold-short heuristic
    # applies ONLY when the document declares zero T1 headings (tiers never mix
    # in one text flow); otherwise the document honestly stays flat.
    marks = [(i, b[2]) for i, b in enumerate(blocks) if b[0] == "p" and b[2] > 0 and b[1]]
    confidence = ""
    if marks:
        doc.structure = "declared"
    else:
        marks = [(i, 1) for i, b in enumerate(blocks) if b[0] == "p" and b[3]]
        if marks:
            doc.structure = "heuristic"
            confidence = "heuristic"

    # sections: ordinal-stable ids, parentage via a level stack; section text =
    # body paragraphs up to the next same-or-higher-level heading (so a parent's
    # text spans its subsections' bodies — FTS over a section finds everything
    # under it; sub-heading LINES are structure, not body, and are excluded)
    mark_at = {i: lvl for i, lvl in marks}
    stack: list = []                        # [(level, ordinal)]
    by_ordinal: dict = {}
    for n, (i, lvl) in enumerate(marks, 1):
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        parent = stack[-1][1] if stack else 0
        texts = []
        for j in range(i + 1, len(blocks)):
            jl = mark_at.get(j)
            if jl is not None:
                if jl <= lvl:
                    break
                continue
            b = blocks[j]
            if b[0] == "p" and b[1]:
                texts.append(b[1])
        section = DocSection(title=blocks[i][1], level=lvl, ordinal=n, parent=parent,
                             confidence=confidence, text="\n".join(texts))
        doc.sections.append(section)
        by_ordinal[n] = section
        stack.append((lvl, n))

    # preamble (before any heading) belongs to the docfile itself; a flat
    # document's whole body lands there — the T3 content capture
    first_mark = marks[0][0] if marks else len(blocks)
    doc.text = "\n".join(b[1] for b in blocks[:first_mark] if b[0] == "p" and b[1])

    # each table's header row -> `columns` on the INNERMOST section open at its
    # position (the docfile when none); the first table per owner wins — one
    # columns attr per node, table NODES are not v1
    mark_ordinal = {i: n for n, (i, _) in enumerate(marks, 1)}
    current = 0
    for j, b in enumerate(blocks):
        if j in mark_ordinal:
            current = mark_ordinal[j]
        elif b[0] == "tbl" and b[1]:
            if current == 0:
                doc.columns = doc.columns or list(b[1])
            elif not by_ordinal[current].columns:
                by_ordinal[current].columns = list(b[1])

    # detected refs scan the WHOLE body text (headings included) — attrs only
    full_text = "\n".join(b[1] for b in blocks if b[0] == "p" and b[1])
    refs = detect_refs(full_text)
    doc.jira_keys = refs.get("jira_keys", [])
    doc.sf_names = refs.get("sf_names", [])
    doc.urls = list(dict.fromkeys(rel_urls + refs.get("urls", [])))
    return doc
