"""Collect on-prem Confluence space(s) into a local page dump (gitignored).

    CONFLUENCE_TOKEN=... python scripts/confluence_collect.py \
        --base-url https://wiki.example.internal --space ENG,OPS --out confluence-dump/

The Personal Access Token is read ONLY from ``$CONFLUENCE_TOKEN`` — never pass it as
a flag (that would leak it into shell history / the process list). The dump holds
real page bodies: keep it local, never commit or egress it. Then build a Confluence
graph from the dump with the ordinary builder (only the Confluence extractor matches):

    python -m graphbuilder confluence-dump/ -o confluence-dump/confluence-graph.json

Re-runs are incremental: unchanged pages (same version) are left untouched, and
pages a COMPLETE listing no longer returns are pruned from the dump (disable with
--no-prune). Blog posts are collected alongside pages unless --pages-only.

Exit codes: 0 clean · 1 fatal setup error · 3 finished with per-space errors or an
incomplete listing (partial dump kept, marked with a .incomplete sentinel).
"""
from __future__ import annotations

import argparse
import sys

from graphbuilder.confluence import collect
from graphbuilder.confluence.collect import CollectError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="confluence_collect", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True,
                    help="instance root, e.g. https://wiki.example.internal")
    ap.add_argument("--space", required=True,
                    help="space key, or comma-separated keys (e.g. ENG,OPS)")
    ap.add_argument("--out", default="confluence-dump",
                    help="dump directory (default: confluence-dump/)")
    ap.add_argument("--per-page", type=int, default=50, help="REST page size (default: 50)")
    ap.add_argument("--insecure", action="store_true",
                    help="disable TLS verification (self-signed on-prem certs) — a knowing choice")
    ap.add_argument("--max-workers", type=int, default=None,
                    help="fetch this many spaces concurrently (default: min(8, n_spaces))")
    ap.add_argument("--pages-only", action="store_true",
                    help="skip blog posts (collected alongside pages by default)")
    ap.add_argument("--no-prune", dest="prune", action="store_false",
                    help="keep dump files for pages a complete listing no longer returns")
    args = ap.parse_args(argv)

    spaces = [s.strip() for s in args.space.split(",") if s.strip()]
    try:
        summary = collect(args.base_url, spaces, args.out,
                          per_page=args.per_page, insecure=args.insecure,
                          max_workers=args.max_workers, prune=args.prune,
                          content_types=("page",) if args.pages_only else ("page", "blogpost"))
    except CollectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for key, n in sorted(summary["spaces"].items()):
        flag = "  INCOMPLETE" if key in summary["incomplete"] else ""
        print(f"  {n:5} written  {key}{flag}", file=sys.stderr)
    print(f"total={summary['pages']} unchanged={summary['unchanged']} "
          f"pruned={len(summary['pruned'])} skipped={len(summary['skipped'])} "
          f"errors={len(summary['errors'])}  ->  {args.out}", file=sys.stderr)
    return 3 if (summary["errors"] or summary["incomplete"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
