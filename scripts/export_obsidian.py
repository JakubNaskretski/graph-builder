"""Export a graph-builder metadata graph as an Obsidian vault (markdown + [[wikilinks]]).

Builds the graph from a force-app, then writes one note per node — relationships
as wikilinks, grouped into type folders — plus an org-map dashboard. Obsidian's
graph view renders the metadata map and each note is a readable hub.

    python scripts/export_obsidian.py <force-app-dir> <vault-out-dir>

The output contains real org names — keep the vault local (gitignore it).
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

from graphbuilder import build_graph

# Display tables only — folder grouping and edge wording, not the source of
# truth for what types exist (that is `model.NODE_TYPES` / `EDGE_TYPES`).
# Anything in the vocabulary but absent here falls back to a derived default (a
# folder named after the type, a title-cased edge label).
_FOLDER_OVERRIDE = {
    "object": "Objects", "field": "Objects", "recordtype": "Objects",
    "listview": "Objects", "globalvalueset": "Objects", "custommetadatarecord": "Objects",
    "apexclass": "Apex", "apexmethod": "Apex", "trigger": "Triggers",
    "flow": "Flows", "flowelement": "Flows",
    "lwc": "LWC", "aura": "LWC",
    "vfpage": "Visualforce", "vfcomponent": "Visualforce",
    "flexipage": "Pages", "layout": "Pages", "quickaction": "Pages",
    "app": "Apps", "tab": "Apps",
    "permissionset": "Security", "profile": "Security", "permsetgroup": "Security",
    "custompermission": "Security", "sharingrule": "Security",
    "queue": "Security", "publicgroup": "Security", "role": "Security",
    "omniscript": "OmniStudio", "integrationprocedure": "OmniStudio",
    "datamapper": "OmniStudio", "flexcard": "OmniStudio",
    "report": "Analytics", "dashboard": "Analytics",
    "approvalprocess": "Automation", "assignmentrule": "Automation",
    "escalationrule": "Automation", "duplicaterule": "Automation",
    "matchingrule": "Automation", "customnotificationtype": "Automation",
    "platformeventchannel": "Automation",
    "label": "Misc", "resource": "Misc", "messagechannel": "Misc", "emailtemplate": "Misc",
}
_EDGE_OUT_OVERRIDE = {
    "calls": "Calls", "on": "Trigger on", "touches": "Touches", "references": "References",
    "grants": "Grants access to", "contains": "Contains", "page-for": "Page for",
    "embeds": "Embeds", "uses": "Uses", "uses-component": "Uses component", "maps": "Maps",
    "extends": "Extends", "implements": "Implements", "reads": "Reads", "writes": "Writes",
    "invocable": "Invokes (apex)", "aura-enabled": "Apex (@AuraEnabled)", "wire": "Wires",
    "async": "Runs async via", "subflow": "Subflow", "formula": "Formula refs",
    "validates": "Validates", "tests": "Tests", "requires": "Requires",
}
_EDGE_IN_OVERRIDE = {
    "calls": "Called by", "on": "Has trigger", "touches": "Touched by",
    "references": "Referenced by", "grants": "Access granted by", "contains": "Member of",
    "page-for": "Has page", "embeds": "Embedded in", "uses": "Used by",
    "uses-component": "Used by component", "maps": "Mapped by",
    "extends": "Extended by", "implements": "Implemented by", "reads": "Read by",
    "writes": "Written by", "invocable": "Invoked by", "aura-enabled": "Exposed to",
    "wire": "Wired by", "async": "Async source for", "subflow": "Called as subflow by",
    "formula": "Referenced in formula", "validates": "Validated by",
    "tests": "Tested by", "requires": "Required by",
}


def _humanize(etype):
    """Readable default for an edge type with no curated wording."""
    return (etype or "").replace("-", " ").replace("_", " ").strip().title() or "Related"


def _folder(ntype):
    """Vault folder for a node type — curated grouping, else a per-type folder."""
    return _FOLDER_OVERRIDE.get(ntype) or (ntype.title() if ntype else "Other")


def _edge_out(etype):
    return _EDGE_OUT_OVERRIDE.get(etype) or _humanize(etype)


def _edge_in(etype):
    return _EDGE_IN_OVERRIDE.get(etype) or (_humanize(etype) + " (in)")


def _name(nid):
    return nid.split("/", 1)[1] if "/" in nid else nid


def _display(nid):
    """Field nodes resolve to their parent object (object-level lookup graph)."""
    if nid.startswith("field/"):
        return "object/" + _name(nid).split(".")[0]
    return nid


def _fname(title):
    return re.sub(r'[\\/:*?"<>|]', "_", title)


def export(force_app, out_dir):
    g = build_graph(force_app)
    nodes = {n["id"]: n for n in g["nodes"]}

    # unique titles (append type only on collision)
    types_by_name = defaultdict(set)
    for nid, n in nodes.items():
        if n["type"] != "field":
            types_by_name[_name(nid)].add(n["type"])

    def title(nid):
        base = _name(nid)
        return base if len(types_by_name[base]) <= 1 else f"{base} ({nodes[nid]['type']})"

    # fields + lookups (object-level)
    fields = defaultdict(list)
    lookups, looked_up = defaultdict(set), defaultdict(set)
    for e in g["edges"]:
        if e["type"] == "field_of":
            fn = nodes.get(e["dst"])
            sn = nodes.get(e["src"])
            if fn and sn:
                fields[e["dst"]].append((sn["label"].split(".")[-1], sn.get("field_type", "")))
        elif e["type"] == "lookup":
            s = _display(e["src"])
            lookups[s].add(e["dst"]); looked_up[e["dst"]].add(s)

    # adjacency (skip field_of/lookup; collapse field endpoints to objects)
    out_adj = defaultdict(lambda: defaultdict(set))
    in_adj = defaultdict(lambda: defaultdict(set))
    for e in g["edges"]:
        if e["type"] in ("field_of", "lookup"):
            continue
        s, d = _display(e["src"]), _display(e["dst"])
        if s != d and s in nodes and d in nodes:
            out_adj[s][e["type"]].add(d); in_adj[d][e["type"]].add(s)

    out = Path(out_dir)
    counts = defaultdict(int)
    degree = {}
    for nid, n in nodes.items():
        if n["type"] == "field":
            continue
        counts[n["type"]] += 1
        t = title(nid)
        L = ["---", f"tags: [sf/{n['type']}]", f"type: {n['type']}"]
        if n.get("external"):
            L.append("external: true")
        if n.get("annotations"):
            L.append(f"annotations: {sorted(n['annotations'])}")
        L += ["---", f"# {t}", "",
              f"*{n['type']}*" + (" · external (referenced, not retrieved)" if n.get("external") else "")]
        body_links = 0

        if n["type"] == "object" and fields.get(nid):
            L += ["", "## Fields"] + [f"- {fn}" + (f" *({ft})*" if ft else "") for fn, ft in sorted(fields[nid])]
        for label, targets in (("Lookups to", lookups.get(nid)), ("Looked up by", looked_up.get(nid))):
            if targets:
                L += ["", f"## {label}"] + [f"- [[{title(x)}]]" for x in sorted(targets, key=title)]
                body_links += len(targets)
        for et, dsts in sorted(out_adj[nid].items()):
            L += ["", f"## {_edge_out(et)}"] + [f"- [[{title(x)}]]" for x in sorted(dsts, key=title)]
            body_links += len(dsts)
        for et, srcs in sorted(in_adj[nid].items()):
            L += ["", f"## {_edge_in(et)}"] + [f"- [[{title(x)}]]" for x in sorted(srcs, key=title)]
            body_links += len(srcs)

        degree[nid] = body_links
        p = out / _folder(n["type"]) / (_fname(t) + ".md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(L) + "\n", "utf-8")

    # dashboard / map-of-content
    idx = ["# Salesforce Org Map", "",
           "Auto-generated from the metadata graph. Open the graph view (Cmd/Ctrl-G).", "",
           "## Counts", ""]
    idx += [f"- **{counts[t]}** {t}" for t in sorted(counts)]
    top = sorted(degree, key=lambda k: degree[k], reverse=True)[:15]
    idx += ["", "## Most-connected nodes", ""]
    idx += [f"- [[{title(nid)}]] — {degree[nid]} links ({nodes[nid]['type']})" for nid in top]
    (out / "_Org Map.md").write_text("\n".join(idx) + "\n", "utf-8")

    return dict(counts), sum(counts.values())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: export_obsidian.py <force-app-dir> <vault-out-dir>")
    counts, total = export(sys.argv[1], sys.argv[2])
    print(f"wrote {total} notes to {sys.argv[2]}")
    for t, c in sorted(counts.items()):
        print(f"  {c:4} {t}")
