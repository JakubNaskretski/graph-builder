"""Build a portable knowledge-base bundle (zip + text) from local sources.

    # 1. collect Confluence first (separate step; token from $CONFLUENCE_TOKEN):
    CONFLUENCE_TOKEN=... python scripts/confluence_collect.py \
        --base-url https://wiki.example.internal --space ENG --out confluence-dump/

    # 2. bundle Salesforce + Confluence + Jira into one knowledge base:
    python scripts/build_bundle.py --salesforce path/to/force-app \
        --confluence confluence-dump/ --jira jira-dump/ --out knowledge-base/

Produces <out>/ (manifest.json, graph.json, content/, README.txt) and <out>.zip.
Any source may be omitted. The bundle contains page bodies, issue text AND
Salesforce source — keep it local; never commit or egress it. (The
`graph-builder pipeline` subcommand wraps this plus the collects.)
"""
from __future__ import annotations

import argparse
import sys

import sys

# allow running straight from a checkout/unpack — no install needed
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from graphbuilder.bundle import build_bundle


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="build_bundle", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salesforce", default=None, help="force-app directory")
    ap.add_argument("--confluence", default=None,
                    help="Confluence dump dir (*.page.json from confluence_collect)")
    ap.add_argument("--jira", default=None,
                    help="Jira dump dir (*.issue.json from jira_collect)")
    ap.add_argument("--out", default="knowledge-base",
                    help="bundle output directory (default: knowledge-base/)")
    ap.add_argument("--no-zip", action="store_true", help="write the bundle dir only, skip the .zip")
    ap.add_argument("--labels", action="store_true", help="join: also match page labels (low confidence)")
    ap.add_argument("--scan-body", action="store_true", help="join: also scan body for *__c API names")
    ap.add_argument("--parallel", action="store_true",
                    help="build the source graphs concurrently")
    args = ap.parse_args(argv)

    if not args.salesforce and not args.confluence and not args.jira:
        ap.error("provide --salesforce, --confluence and/or --jira")

    try:
        summary = build_bundle(
            args.out, salesforce=args.salesforce, confluence_dump=args.confluence,
            jira_dump=args.jira,
            zip_path=False if args.no_zip else None, parallel=args.parallel,
            join_opts={"match_labels": args.labels, "scan_body": args.scan_body})
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    g = summary["manifest"]["graph"]
    print(f"nodes={g['nodes']} edges={g['edges']} documents={g['documents_edges']}"
          f"  ->  {summary['out_dir']}" + (f" + {summary['zip']}" if summary["zip"] else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
