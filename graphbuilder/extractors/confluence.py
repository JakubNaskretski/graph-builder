"""Confluence page extractor — ``*.page.json`` dumps from ``confluence.collect``.

Emits the intra-Confluence graph for one page: a ``page`` node (carrying body text
+ metadata attrs) plus its space, attachments, labels and mentioned/authoring
users, wired by ``child-of`` / ``links-to`` / ``attaches`` / ``labeled`` /
``mentions`` / ``authored-by``. This is the SEPARATE Confluence graph — it never
emits Salesforce edges; wiring pages to the SF nodes they document is the
deliberate :func:`graphbuilder.confluence.join` step.

Node ids: ``space/<KEY>`` · ``page/<KEY>/<title>`` ·
``attachment/<KEY>/<title>/<filename>`` · ``confluencelabel/<name>`` ·
``confluenceuser/<key>``. Pages are addressed by (space, title) because that is how
storage-format links reference them (``<ri:page ri:content-title ri:space-key>``),
so a ``links-to`` edge resolves to the real page node when its dump is present, or
to an external stub when it is not — exactly like a Salesforce cross-file reference.
"""
from __future__ import annotations

from pathlib import Path

from ..confluence.parse import parse_page
from ..core import node, raw_edge


def _slug(s: str) -> str:
    """Collapse path separators so a title/filename is safe inside a ``type/name``
    id (the name segment must not introduce stray ``/`` hops). Applied identically
    when building a node id and when naming a link target, so the two always match."""
    return (s or "").replace("/", "_").replace("\\", "_").strip()


def _page_name(space: str, title: str) -> str:
    """The ``<space>/<title>`` name segment shared by a page node id and any
    ``links-to`` / ``child-of`` edge that targets it."""
    return f"{_slug(space)}/{_slug(title)}"


class ConfluenceExtractor:
    source = "confluence"

    def handles(self, path: Path) -> bool:
        return path.name.endswith(".page.json")

    def extract(self, path: Path):
        p = parse_page(path)
        space = _slug(p.space_key) or "_"
        title = p.title or p.id or path.stem
        page_name = _page_name(p.space_key or space, title)
        pid = f"page/{page_name}"

        # --- page node (structure + the deliberate body-text content capture) ---
        attrs = {"source": "confluence"}
        if p.id:
            attrs["page_id"] = p.id
        if p.space_key:
            attrs["space_key"] = p.space_key
        if p.version:
            attrs["version"] = p.version
        if p.url:
            attrs["url"] = p.url
        if p.urls:
            attrs["urls"] = list(dict.fromkeys(p.urls))
        if p.body_text:
            attrs["text"] = p.body_text
        nodes = [node(pid, "page", title, **attrs)]

        # space node + child-of (parent page if any, else the space)
        sid = f"space/{_slug(p.space_key or space)}"
        nodes.append(node(sid, "space", p.space_key or space, source="confluence"))

        edges: list[dict] = []
        seen: set[tuple] = set()

        def add_edge(etype, to_kind, to_name):
            if not to_name:
                return
            key = (etype, to_kind, to_name)
            if key in seen:
                return
            seen.add(key)
            edges.append(raw_edge(pid, etype, to_kind, to_name))

        if p.parent_title:
            add_edge("child-of", "page", _page_name(p.space_key or space, p.parent_title))
        else:
            add_edge("child-of", "space", _slug(p.space_key or space))

        # page -> page links (default to this page's space when the link omits one);
        # skip a self-link.
        for link_title, link_space in p.links:
            target = _page_name(link_space or p.space_key or space, link_title)
            if target != page_name:
                add_edge("links-to", "page", target)

        # attachments — emit the node (real, with its filename label) + the edge
        for fn in dict.fromkeys(p.attachments):  # de-dup, preserve order
            a_name = f"{_page_name(p.space_key or space, title)}/{_slug(fn)}"
            nodes.append(node(f"attachment/{a_name}", "attachment", fn, source="confluence"))
            add_edge("attaches", "attachment", a_name)

        # labels + users are shared nodes (first emitter wins in the registry), so
        # "pages with label X" / "pages mentioning user Y" fall out across the graph.
        for lbl in dict.fromkeys(p.labels):
            nodes.append(node(f"confluencelabel/{lbl}", "confluencelabel", lbl, source="confluence"))
            add_edge("labeled", "confluencelabel", lbl)

        for user in dict.fromkeys(p.mentions):
            nodes.append(node(f"confluenceuser/{user}", "confluenceuser", user, source="confluence"))
            add_edge("mentions", "confluenceuser", user)

        if p.author:
            nodes.append(node(f"confluenceuser/{p.author}", "confluenceuser", p.author, source="confluence"))
            add_edge("authored-by", "confluenceuser", p.author)

        return nodes, edges


EXTRACTORS = [ConfluenceExtractor()]
