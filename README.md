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

### One-command pipeline

Every stage — (optional) Confluence/Jira collects → per-source builds → joins →
bundle — behind a single command, made for wrapping as an agent skill or a cron
refresh:

```sh
graph-builder pipeline --salesforce force-app --confluence-dump confluence-dump --out kb
graph-builder pipeline --config pipeline.json        # same options from a JSON file (flags win)
```

Exit codes (all CLI modes): `0` clean · `1` fatal (bad input/setup) · `2` usage ·
`3` finished but the run recorded errors — output is still written, so a harness
can key off the code without losing the artifact.

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

The skip list is policy, not an accident: **NamedCredential, ExternalCredential,
RemoteSiteSetting, ConnectedApp, certs and IframeWhiteListUrlSettings** carry
endpoints/secrets and must never gain an extractor. Settings and `*Translations`
metadata are skipped as low-signal noise. Managed-package metadata (`ns__`
prefixed) is graphed like any other when it is present in the retrieve; Data
Cloud objects (`__dlm`/`__dll`) are out of scope. Every emitted node carries a
`source_path` back to the file that defined it (decomposed children point at
their own `fields/…`/`recordTypes/…` files), so a consumer can always step from
the graph into the underlying source.

## Confluence (a second, joinable source)
graph-builder can also ingest an on-prem **Confluence** space as its own graph and
— deliberately, on demand — link pages to the Salesforce entities they document.
See `graphbuilder/confluence/`.

```sh
# 1. collect a space into a local, gitignored dump (token from $CONFLUENCE_TOKEN ONLY)
CONFLUENCE_TOKEN=… python scripts/confluence_collect.py \
    --base-url https://wiki.example.internal --space ENG,OPS --out confluence-dump/

# 2. build a Confluence graph with the ordinary builder — only the Confluence
#    extractor matches *.page.json, so you get a Confluence-only graph
python -m graphbuilder confluence-dump/ -o confluence-dump/confluence-graph.json

# 3. join it to a Salesforce graph — page --documents--> the object/class it references
python scripts/confluence_join.py confluence-dump/confluence-graph.json sf-graph.json -o joined.json
```

- **Nodes** `space` · `page` (id-keyed — rename-stable; blog posts too, marked
  `content_type`) · `attachment` · `confluencelabel` · `confluenceuser`;
  **edges** `child-of` · `links-to` · `embeds` (include/excerpt-include macros) ·
  `attaches` · `labeled` · `mentions` · `authored-by` — all parsed from the
  storage-format markup, not guessed from prose.
- **Re-collection is incremental.** Unchanged pages (same `version.number`) are
  not rewritten; pages a complete listing no longer returns are pruned from the
  dump (`--no-prune` to keep them); a space whose listing aborted is marked with
  a `.incomplete` sentinel and never pruned.
- **The join is separate and auditable.** `graphbuilder.confluence.join(confluence,
  salesforce)` returns `documents` cross-edges tagged with `via`/`confidence` (org
  URLs + exact title match by default; labels / body scan opt-in), so messy
  Confluence content never contaminates the Salesforce graph — you keep only the
  links you trust. `merge(...)` unions both graphs when you want one.

> **Content & confidentiality.** Unlike the Salesforce extractors (names/structure
> only), the Confluence source **captures page body text** (agent-facing knowledge).
> Every dump and any built Confluence / joined graph therefore holds real content —
> they are **gitignored and must never be committed or egressed**.

## Jira (a third, joinable source)

Same architecture as Confluence (collect → parse → extractor → join), same auth
model (Data Center / Server PAT as a Bearer token, read ONLY from `$JIRA_TOKEN`),
same incremental dump semantics (unchanged `updated` → untouched; complete
listing → prune; aborted listing → `.incomplete`, never pruned).

```sh
# 1. collect project(s) into a local, gitignored dump
JIRA_TOKEN=… python scripts/jira_collect.py \
    --base-url https://jira.example.internal --project ACME,OPS --out jira-dump/
# --remote-links also fetches each issue's remote links (1 extra request/issue —
# the strongest issue->Confluence-page signal)

# 2. build a Jira graph with the ordinary builder
python -m graphbuilder jira-dump/ -o jira-dump/jira-graph.json
```

- **Nodes** `jiraproject` · `jiraissue` (keyed by Jira's stable issue key; summary
  as label, description as text) · `jiralabel` · `jirauser`; **edges** `child-of`
  (issue→project, subtask→parent) · `links-to` (issue links + subtasks) ·
  `labeled` · `assigned-to` · `authored-by` · `mentions`.
- **Joins are separate and auditable**, like Confluence's:
  `graphbuilder.jira.join(jira, sf)` → issue -`documents`-> SF entity (Lightning
  URLs in the description; summary-match deliberately off by default);
  `join_confluence(jira, confluence)` → issue <-`links-to`-> page (page URLs in
  the issue; jira macros on the page). All edges carry `via`/`confidence`.
- Scope: Jira DC/Server 8.14+ (PAT). Jira Cloud and the agile API
  (boards/sprints) are out of scope, matching the Confluence source.
- Same confidentiality posture: dumps and built Jira graphs hold real issue text —
  gitignored, never committed or egressed.

## Knowledge-base bundle (portable, zip + text, no DB)
Package one or both sources into a self-contained **knowledge base** — a zip of
text/JSON an on-prem agent can navigate offline. Two layers joined by pointers: a lean
**graph** (structure + a `content` pointer + short `excerpt` per node) and a **content
store** of flat files the graph points into. Under the no-DB constraint the graph *is*
the retrieval index — following edges gives structural recall a flat dump can't.

```sh
python scripts/build_bundle.py --salesforce path/to/force-app \
    --confluence confluence-dump/ --out knowledge-base/
# or all three sources behind one command:
graph-builder pipeline --salesforce force-app --confluence-dump confluence-dump \
    --jira-dump jira-dump --out knowledge-base
```

```
knowledge-base/                         (+ knowledge-base.zip)
├── manifest.json   # provenance, counts, schema version
├── graph.json      # nodes (structure + content pointers + excerpt) + edges
├── content/
│   ├── confluence/<SPACE>/<id>.txt     # page body (plain text)
│   ├── confluence/<SPACE>/<id>.xhtml   # raw storage (tables, macros, diagram refs)
│   ├── jira/<PROJECT>/<KEY>.txt        # issue summary + description
│   └── salesforce/<path>               # copied source units
└── README.txt
```

- Any source may be omitted; every present pair is joined (page→SF / issue→SF
  `documents`; issue↔page `links-to`).
- Full body text lives in `content/*.txt`, **not** in `graph.json` (only a short
  excerpt) — the graph stays small and the agent reads only what it needs.
- Only source files that produced graph nodes are copied, so leakage-prone types
  nothing graphs (Named Credentials, Static Resources) are never bundled.

> **Confidential.** A bundle holds real page bodies and Salesforce source. The output
> directory and its `.zip` are gitignored — keep them local, never commit or egress.

## Agent classification (read-cold)
The deterministic `join` links pages to SF nodes by URL/title. For deeper, *verified*
classification — what a page is actually about, which objects/process it documents — an
on-prem **agent reads each page and decides**, using the methods we expose. The LLM lives
in the agent; the library stays deterministic.

```python
from graphbuilder import load_graph, save_graph, find_nodes, node_text
from graphbuilder.confluence import apply_classifications

g = load_graph("knowledge-base/graph.json")
verdicts = []
for page in [n for n in g["nodes"] if n["type"] == "page"]:
    text = node_text(page, root="knowledge-base")          # read the content pointer
    # ...the agent reads `text`, names entities, and resolves them, e.g.
    #   find_nodes(g, "the Billing object", types=["object"]) -> object/Billing__c
    verdicts.append({"page_id": page["id"], "process_type": "order-to-cash",
                     "documents": [{"target": "object/Billing__c",
                                    "confidence": "high", "evidence": "…quote…"}]})
g, report = apply_classifications(g, verdicts)              # validated, non-mutating
save_graph(g, "knowledge-base/graph.json")
```

- `find_nodes(graph, query, types=…)` resolves a name found in text → node id(s)
  (ranked, fuzzy, stdlib-only); `node_text(node, root)` reads a node's content.
- `apply_classifications` writes `documents` edges with **provenance** — `via`
  (`agent`/`url`/`title`) + `confidence` + `evidence` — so every link is auditable. An
  agent verdict supersedes a syntactic edge for the same pair; an unknown id is skipped
  and reported, never fabricated. `scripts/classify_apply.py` applies an agent-produced
  `verdicts.json` deterministically.

## Parallelism
- **Collector** fetches multiple spaces concurrently (`--max-workers`, default
  `min(8, n_spaces)`); pagination within a space stays sequential. I/O-bound → a real
  speedup. Output is keyed by page id, so order never changes the result.
- **Bundle** `--parallel` overlaps the (parser-free) Confluence build with the Salesforce
  build. The Apex tree-sitter parser is *unsendable* (pinned to its origin thread), so the
  SF build stays on the main thread and only Confluence is offloaded; the merge order is
  fixed, so the graph is identical to a serial build. True multi-core CPU parallelism
  (per-file multiprocessing) is deferred.
