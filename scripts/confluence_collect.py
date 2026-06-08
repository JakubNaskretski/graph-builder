"""Collect on-prem Confluence space(s) into a local page dump (gitignored).

    CONFLUENCE_TOKEN=... python scripts/confluence_collect.py \
        --base-url https://wiki.example.internal --space ENG,OPS --out confluence-dump/

The Personal Access Token is read ONLY from ``$CONFLUENCE_TOKEN`` — never pass it as
a flag (that would leak it into shell history / the process list). The dump holds
real page bodies: keep it local, never commit or egress it. Then build a Confluence
graph from the dump with the ordinary builder (only the Confluence extractor matches):

    python -m graphbuilder confluence-dump/ -o confluence-dump/confluence-graph.json
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
    args = ap.parse_args(argv)

    spaces = [s.strip() for s in args.space.split(",") if s.strip()]
    try:
        summary = collect(args.base_url, spaces, args.out,
                          per_page=args.per_page, insecure=args.insecure)
    except CollectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for key, n in sorted(summary["spaces"].items()):
        print(f"  {n:5} pages  {key}", file=sys.stderr)
    print(f"total={summary['pages']} skipped={len(summary['skipped'])} "
          f"errors={len(summary['errors'])}  ->  {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
