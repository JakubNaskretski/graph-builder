# graph-builder

Parse a Salesforce `force-app` into a typed **metadata graph** (nodes + edges) you
can query, traverse (depth-limited, cycle-safe), and visualize.

```python
from graphbuilder import build_graph, save_graph, load_graph
g = build_graph("path/to/force-app")   # {"nodes", "edges", "unresolved", "errors"}
save_graph(g, "graph.json")            # deterministic JSON (stable diffs)
g = load_graph("graph.json")           # reload without re-parsing
```

### Digest a single file

`build_file` parses just one metadata file. Two independent knobs control what you
get back — **how far** from the file (`levels`) and **which kinds** of node
(`types`):

```python
from graphbuilder import build_file

# levels: how many levels deep to map, counted from the source file (1-based)
build_file("force-app/classes/MyClass.cls", levels=1)  # just the file (class + its methods)
build_file("force-app/classes/MyClass.cls", levels=2)  # + one hop (objects/classes it refs)
build_file("force-app/classes/MyClass.cls", levels=3)  # + next hop (those objects' fields)

# types: keep only certain node types (e.g. map an Apex file's methods, not objects)
build_file("force-app/classes/MyClass.cls", types="apexmethod")
build_file("force-app/classes/MyClass.cls", levels=2, types=["apexmethod", "object"])

# repo: resolve the file's edges against the whole tree so they hit REAL nodes
# (needed for, e.g., reaching an object's fields at level 3 — a stub has none)
build_file("force-app/classes/MyClass.cls", levels=3, repo="force-app")
```

- `levels` — levels deep from the file (`1` = just the file's own nodes; `None`
  default = no limit). Distance is undirected and cycle-safe, so a level is one
  hop regardless of edge direction (class →`references`→ object →`field_of`→ field).
- `types` — node-type allowlist (a string or iterable); edges are kept only
  between surviving nodes.
- `repo` — optional `force-app` root; resolves off-file targets to real nodes
  instead of external stubs.

Or from the command line:

```sh
python -m graphbuilder path/to/force-app -o graph.json    # also: graph-builder ...
python -m graphbuilder path/to/MyClass.cls --levels 2 --repo path/to/force-app
python -m graphbuilder path/to/MyClass.cls --types apexmethod
```

## Layers
- **Parsers** (`graphbuilder/salesforce.py`, `omnistudio.py`): per-unit metadata parsers
  — objects + fields, Apex, triggers, flows, LWC, flexipages, permission sets /
  profiles / groups, OmniStudio.
- **Extractors** (`graphbuilder/extractors/*`): one module per metadata type, each
  emitting graph nodes + raw edges (`calls` / `references` / `invocable` /
  `aura-enabled` / `wire` / …). Auto-discovered — drop in a module, no wiring.
- **Core** (`graphbuilder/core.py`, `resolvers.py`): a two-pass build.
  1. *Extract* — each file is dispatched to the extractor that handles it, which
     emits nodes and *raw edges* (targets named logically, not yet resolved).
  2. *Resolve* — each raw edge's `(to_kind, to_name)` becomes a node id via the
     resolver for that kind; targets outside the repo become external stubs.

  Nothing raises: an edge with no resolver or no match lands in `unresolved`, and
  an extractor that throws lands in `errors`. The result is always
  `{nodes, edges, unresolved, errors}`.
- **Analysis** (`graphbuilder/analyze.py`): read-only queries over a built graph —
  `impact` (what depends on a node), `orphans`, `permission_reachability`,
  `graph_summary`. Cycle-safe and bounded; never mutates the graph.
- **Persistence** (`graphbuilder/persistence.py`): `save_graph` / `load_graph` /
  `to_json` / `from_json` — deterministic JSON so a build can be cached, shipped,
  or diffed across commits.

An Obsidian-vault exporter (`scripts/export_obsidian.py`) renders the graph as
markdown + `[[wikilinks]]`; its folders/labels bind to the model vocabulary, so
new node/edge types render automatically. Keep any exported vault local (it
contains org-derived names).

## Traversal (robust by design)
`graphbuilder.model.traverse(graph, start, max_depth=N)` and `subgraph(...)` are
**depth-limited** (the N-level cap) and **cycle-safe** (a visited set) — a reference
cycle or broken ref can never cause an infinite loop, and never obstructs the graph.
`find_cycles(graph)` reports any cycles diagnostically without affecting traversal.

## Confidentiality by design
Extractors emit only structural **names and relationships** — never field values,
record data, formulas, endpoints, or credentials. Leakage-prone metadata (Named
Credentials, Static Resources) is deliberately not graphed, and SOQL/SOSL is walked
for object/field identifiers only. Tests use fictional sample data.
