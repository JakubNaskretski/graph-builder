"""Join a Confluence graph to a Salesforce graph (page -> SF node it documents).

    python scripts/confluence_join.py confluence-graph.json sf-graph.json -o joined.json

Both inputs are graphs previously saved with ``graphbuilder.save_graph``. By default
writes a MERGED graph (Salesforce + Confluence + ``documents`` cross-edges) to ``-o``
(or stdout); ``--edges-only`` prints just the cross-edges. Matching is conservative
(org URLs + exact title); ``--labels`` / ``--scan-body`` widen it, ``--min-confidence``
trims it. Every cross-edge carries ``via`` + ``confidence`` so you keep only what you
trust. The output carries org-derived names AND page content — keep it local.
"""
from __future__ import annotations

import argparse
import json
import sys

from graphbuilder import load_graph, save_graph
from graphbuilder.confluence import join, merge

_RANK = {"low": 1, "medium": 2, "high": 3}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="confluence_join", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("confluence_graph", help="a built Confluence graph JSON")
    ap.add_argument("salesforce_graph", help="a built Salesforce graph JSON")
    ap.add_argument("-o", "--out", default=None, help="write the merged graph here (else stdout)")
    ap.add_argument("--edges-only", action="store_true",
                    help="emit only the cross-edges as JSON (to stdout)")
    ap.add_argument("--labels", action="store_true", help="also match page labels (low confidence)")
    ap.add_argument("--scan-body", action="store_true",
                    help="also scan body text for *__c-style API names")
    ap.add_argument("--min-confidence", choices=("low", "medium", "high"), default="low",
                    help="drop cross-edges below this confidence (default: low = keep all)")
    args = ap.parse_args(argv)

    cg = load_graph(args.confluence_graph)
    sg = load_graph(args.salesforce_graph)
    cross = join(cg, sg, match_labels=args.labels, scan_body=args.scan_body)
    floor = _RANK[args.min_confidence]
    cross = [e for e in cross if _RANK.get(e.get("confidence"), 0) >= floor]

    by_conf: dict[str, int] = {}
    for e in cross:
        by_conf[e["confidence"]] = by_conf.get(e["confidence"], 0) + 1

    if args.edges_only:
        sys.stdout.write(json.dumps(cross, indent=2, ensure_ascii=False) + "\n")
    else:
        merged = merge(sg, cg, cross)
        if args.out:
            save_graph(merged, args.out)
        else:
            sys.stdout.write(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")

    print(f"documents edges: {len(cross)} "
          + " ".join(f"{k}={v}" for k, v in sorted(by_conf.items()))
          + (f"  ->  {args.out}" if args.out and not args.edges_only else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
