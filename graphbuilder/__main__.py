"""Command-line entry point: build a metadata graph and save it as JSON.

    python -m graphbuilder <force-app-dir> [-o graph.json]
    graph-builder <force-app-dir> [-o graph.json]      # via the console script

Point it at a single FILE to digest just that file. ``--levels`` controls how many
levels deep to map from the file (1 = just the file's own nodes, 2 = + one hop,
…); ``--types`` restricts the result to given node types; ``--repo`` adds full-tree
context so its edges resolve to real nodes instead of stubs:

    graph-builder path/to/MyClass.cls --levels 2 --repo path/to/force-app
    graph-builder path/to/MyClass.cls --types apexmethod

With no ``-o`` the JSON is written to stdout. A short summary (node/edge/
unresolved/error counts) is always printed to stderr so a redirected stdout stays
clean. Building never raises: unhandled files are skipped and per-extractor
failures land in the graph's ``errors`` list (reflected in the summary).

By default the output is **redacted**: Confluence page bodies (the one free-text
value a node can carry) are dropped so a plain dump can't spill page text. Pass
``--with-text`` to keep them inline.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import build_graph, build_file
from .analyze import graph_summary
from .persistence import save_graph, to_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="graph-builder",
        description="Build a Salesforce force-app metadata graph and emit it as JSON.",
    )
    parser.add_argument(
        "path", help="a force-app directory to scan, or a single metadata file to digest",
    )
    parser.add_argument(
        "-o", "--out", default=None,
        help="output JSON file (default: write to stdout)",
    )
    parser.add_argument(
        "-l", "--levels", type=int, default=None,
        help="single-file digest: levels deep to map from the file (1=just the "
             "file's own nodes, 2=+one hop, ...). Ignored when scanning a directory.",
    )
    parser.add_argument(
        "-t", "--types", default=None,
        help="single-file digest: comma-separated node-type allowlist "
             "(e.g. 'apexmethod,object'). Keeps only nodes of these types.",
    )
    parser.add_argument(
        "--repo", default=None,
        help="single-file digest: force-app root for full cross-file resolution "
             "context (edges resolve to real nodes, not stubs).",
    )
    parser.add_argument(
        "--with-text", action="store_true",
        help="keep inline Confluence page body text in the output (default: "
             "redact it — bodies are the one free-text value a node can carry).",
    )
    args = parser.parse_args(argv)

    if Path(args.path).is_file():
        types = [t.strip() for t in args.types.split(",") if t.strip()] if args.types else None
        graph = build_file(args.path, levels=args.levels, types=types, repo=args.repo)
    else:
        graph = build_graph(args.path)

    redact = not args.with_text
    if args.out:
        save_graph(graph, args.out, redact_text=redact)
    else:
        sys.stdout.write(to_json(graph, redact_text=redact) + "\n")

    summary = graph_summary(graph)
    print(
        f"nodes={sum(summary['node_counts'].values())} "
        f"edges={sum(summary['edge_counts'].values())} "
        f"unresolved={len(graph.get('unresolved', []))} "
        f"errors={len(graph.get('errors', []))}"
        + (f"  ->  {args.out}" if args.out else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
