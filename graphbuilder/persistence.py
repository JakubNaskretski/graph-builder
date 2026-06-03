"""Persistence — save / load a built graph as JSON.

A built graph is the plain dict ``{"nodes", "edges", "unresolved", "errors"}``
returned by :func:`graphbuilder.build_graph`. Persisting it means a build can be
cached, shipped to another tool, diffed across commits, or reloaded without
re-parsing the whole ``force-app``.

The on-disk form is a small wrapper carrying a ``version`` so the format can
evolve::

    {"version": 1, "nodes": [...], "edges": [...], "unresolved": [...], "errors": [...]}

Output is **deterministic**: nodes are sorted by id and edges by
``(src, type, dst)``, so two builds of the same metadata produce byte-identical
files (clean diffs). Loading is tolerant — a bare ``{"nodes", "edges"}`` dict, or
one missing the optional ``unresolved`` / ``errors`` keys, still loads.

Confidentiality: this layer only serialises what is already in the graph (names
and structure, never field/record values). Node ids are org-derived, so keep any
file built from a real org out of the repo.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_VERSION = 1
_GRAPH_KEYS = ("nodes", "edges", "unresolved", "errors")


def to_jsonable(graph) -> dict:
    """Normalise ``graph`` to the versioned, deterministically-ordered dict that
    gets written to disk. Missing optional keys default to empty lists. Never
    mutates the input."""
    graph = graph or {}
    nodes = sorted(
        (n for n in graph.get("nodes", []) or [] if isinstance(n, dict)),
        key=lambda n: str(n.get("id", "")),
    )
    edges = sorted(
        (e for e in graph.get("edges", []) or [] if isinstance(e, dict)),
        key=lambda e: (str(e.get("src", "")), str(e.get("type", "")), str(e.get("dst", ""))),
    )
    return {
        "version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
        "unresolved": list(graph.get("unresolved", []) or []),
        "errors": list(graph.get("errors", []) or []),
    }


def to_json(graph, indent: int = 2) -> str:
    """Serialise ``graph`` to a JSON string (deterministic ordering)."""
    return json.dumps(to_jsonable(graph), indent=indent, sort_keys=True, ensure_ascii=False)


def from_json(text: str) -> dict:
    """Parse a JSON string into a graph dict ``{nodes, edges, unresolved, errors}``.

    Tolerant of the bare or partial shapes described in the module docstring.
    The ``version`` wrapper key is dropped from the returned graph."""
    data = json.loads(text)
    if not isinstance(data, dict):
        return {k: [] for k in _GRAPH_KEYS}
    return {k: list(data.get(k, []) or []) for k in _GRAPH_KEYS}


def save_graph(graph, path) -> Path:
    """Write ``graph`` to ``path`` as JSON (creating parent dirs). Returns the Path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(graph), encoding="utf-8")
    return path


def load_graph(path) -> dict:
    """Read a graph previously written by :func:`save_graph` (or any compatible
    JSON file). Returns ``{nodes, edges, unresolved, errors}``."""
    return from_json(Path(path).read_text(encoding="utf-8"))
