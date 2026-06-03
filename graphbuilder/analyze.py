"""Read-only queries over a built graph ``{nodes, edges}``.

Diagnostics computed after the graph is built; they never mutate it. The BFS
helpers reuse ``model.traverse`` and ``model.find_cycles``, so every query is
cycle-safe and bounded. They read only node ids/types and structural edges, never
field values, formulas, or other content.

Public API:
  - ``impact(graph, node_id, max_depth=None)`` — nodes that transitively DEPEND ON
    ``node_id`` (reverse/incoming edge direction).
  - ``orphans(graph, types=None)`` — node ids with no incoming edges (excluding
    ``external`` nodes).
  - ``permission_reachability(graph, target_id)`` — permissionset/profile ids that
    grant access to ``target_id`` directly or via a containing permset group.
  - ``graph_summary(graph)`` — counts of nodes/edges by type plus cycle/orphan totals.
"""
from __future__ import annotations

from collections import Counter

from . import model

# Edge types whose target is a permission principal grant / group membership.
_GRANTS = "grants"
_CONTAINS = "contains"
_PRINCIPAL_TYPES = frozenset({"permissionset", "profile"})


def _nodes_by_id(graph):
    """``{id: node}`` map, tolerant of odd/missing node dicts."""
    out = {}
    for n in (graph or {}).get("nodes", []) or []:
        try:
            nid = n.get("id")
        except AttributeError:
            continue
        if nid is not None:
            out.setdefault(nid, n)
    return out


def impact(graph, node_id, max_depth=None):
    """Nodes that transitively DEPEND ON ``node_id``.

    Edges read "from depends on to", so the dependants of ``node_id`` are the
    nodes reachable by following edges in the **reverse (incoming)** direction.
    Returns a list of ``{"id": <id>, "depth": <int>}`` ordered by increasing
    depth (BFS order), excluding ``node_id`` itself. ``max_depth=None`` is
    unbounded but still cycle-safe. Returns ``[]`` for an unknown ``node_id``.
    """
    if not graph or node_id is None:
        return []
    if node_id not in _nodes_by_id(graph):
        return []
    out = []
    for nid, depth, _etype in model.traverse(
        graph, node_id, direction="in", max_depth=max_depth
    ):
        out.append({"id": nid, "depth": depth})
    return out


def orphans(graph, types=None):
    """Node ids with NO incoming edge, excluding nodes marked ``external`` True.

    An orphan is a node nothing else depends on (no edge points *at* it). Nodes
    flagged ``external`` (resolver-created stubs for things outside the repo,
    e.g. standard objects) are never reported. ``types`` optionally restricts the
    result to the given node type(s) — pass a string or an iterable of strings.
    """
    if not graph:
        return []
    nbyid = _nodes_by_id(graph)

    type_filter = None
    if types is not None:
        type_filter = {types} if isinstance(types, str) else set(types)

    has_incoming = set()
    for e in graph.get("edges", []) or []:
        dst = e.get("dst") if isinstance(e, dict) else None
        if dst is not None:
            has_incoming.add(dst)

    out = []
    for nid, n in nbyid.items():
        if nid in has_incoming:
            continue
        if n.get("external") is True:
            continue
        if type_filter is not None and n.get("type") not in type_filter:
            continue
        out.append(nid)
    return sorted(out)


def permission_reachability(graph, target_id):
    """Sorted permissionset/profile ids granting access to ``target_id``.

    A principal reaches ``target_id`` when:
      - it has a ``grants`` edge directly to ``target_id``, OR
      - it is a permissionset contained (``contains`` edge) by a permsetgroup
        whose member permissionset grants ``target_id`` (group-mediated).
    Returns a sorted, de-duplicated list of ``permissionset``/``profile`` node
    ids. Unknown / unspecified ``target_id`` yields ``[]``.
    """
    if not graph or target_id is None:
        return []
    nbyid = _nodes_by_id(graph)

    # Direct granters: principals with a `grants` edge straight at the target.
    granters = set()
    for e in graph.get("edges", []) or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") != _GRANTS or e.get("dst") != target_id:
            continue
        src = e.get("src")
        if src is None:
            continue
        if nbyid.get(src, {}).get("type") in _PRINCIPAL_TYPES:
            granters.add(src)

    # Group-mediated: any permsetgroup that `contains` a granting permissionset
    # keeps that permissionset in the result set (it is itself a principal). This
    # also surfaces members reachable only through group membership.
    contained_principals = set()
    for e in graph.get("edges", []) or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") != _CONTAINS:
            continue
        src, dst = e.get("src"), e.get("dst")
        if nbyid.get(src, {}).get("type") != "permsetgroup":
            continue
        if dst in granters and nbyid.get(dst, {}).get("type") in _PRINCIPAL_TYPES:
            contained_principals.add(dst)

    return sorted(granters | contained_principals)


def graph_summary(graph):
    """Summary dict: ``node_counts``, ``edge_counts``, ``cycle_count``, ``orphan_count``.

    ``node_counts`` / ``edge_counts`` map each type to its tally. ``cycle_count``
    uses ``model.find_cycles`` (purely diagnostic; traversal stays bounded
    regardless). ``orphan_count`` is the number of non-external nodes with no
    incoming edge.
    """
    graph = graph or {}
    node_counts = Counter()
    for n in graph.get("nodes", []) or []:
        if isinstance(n, dict):
            node_counts[n.get("type")] += 1

    edge_counts = Counter()
    for e in graph.get("edges", []) or []:
        if isinstance(e, dict):
            edge_counts[e.get("type")] += 1

    try:
        cycle_count = len(model.find_cycles(graph))
    except Exception:
        cycle_count = 0

    return {
        "node_counts": dict(node_counts),
        "edge_counts": dict(edge_counts),
        "cycle_count": cycle_count,
        "orphan_count": len(orphans(graph)),
    }
