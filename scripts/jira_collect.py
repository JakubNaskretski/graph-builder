"""Collect on-prem Jira project(s) into a local issue dump (gitignored).

    JIRA_TOKEN=... python scripts/jira_collect.py \
        --base-url https://jira.example.internal --project ACME,OPS --out jira-dump/

The Personal Access Token is read ONLY from ``$JIRA_TOKEN`` — never pass it as a
flag (that would leak it into shell history / the process list). The dump holds
real issue summaries + descriptions: keep it local, never commit or egress it.
Then build a Jira graph from the dump with the ordinary builder (only the Jira
extractor matches):

    python -m graphbuilder jira-dump/ -o jira-dump/jira-graph.json

Re-runs are incremental: unchanged issues (same `updated` timestamp) are left
untouched, and issues a COMPLETE listing no longer returns are pruned from the
dump (disable with --no-prune). --remote-links additionally fetches each issue's
remote links (one extra request per issue; the strongest issue->Confluence-page
signal for the join).

Exit codes: 0 clean · 1 fatal setup error · 3 finished with per-project errors or
an incomplete listing (partial dump kept, marked with a .incomplete sentinel).
"""
from __future__ import annotations

import argparse
import sys

from graphbuilder.jira import collect
from graphbuilder.jira.collect import CollectError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="jira_collect", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True,
                    help="instance root, e.g. https://jira.example.internal")
    ap.add_argument("--project", required=True,
                    help="project key, or comma-separated keys (e.g. ACME,OPS)")
    ap.add_argument("--out", default="jira-dump",
                    help="dump directory (default: jira-dump/)")
    ap.add_argument("--per-page", type=int, default=50, help="REST page size (default: 50)")
    ap.add_argument("--insecure", action="store_true",
                    help="disable TLS verification — last resort; prefer --ca-bundle")
    ap.add_argument("--ca-bundle", default=None,
                    help="PEM file of a private CA to trust (keeps full TLS verification)")
    ap.add_argument("--max-workers", type=int, default=None,
                    help="fetch this many projects concurrently (default: min(8, n))")
    ap.add_argument("--remote-links", action="store_true",
                    help="also fetch each issue's remote links (one request per issue)")
    ap.add_argument("--no-prune", dest="prune", action="store_false",
                    help="keep dump files for issues a complete listing no longer returns")
    args = ap.parse_args(argv)

    projects = [p.strip() for p in args.project.split(",") if p.strip()]
    try:
        summary = collect(args.base_url, projects, args.out,
                          per_page=args.per_page, insecure=args.insecure,
                          ca_bundle=args.ca_bundle, max_workers=args.max_workers,
                          remote_links=args.remote_links, prune=args.prune)
    except CollectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for key, n in sorted(summary["projects"].items()):
        flag = "  INCOMPLETE" if key in summary["incomplete"] else ""
        print(f"  {n:5} written  {key}{flag}", file=sys.stderr)
    print(f"total={summary['issues']} unchanged={summary['unchanged']} "
          f"pruned={len(summary['pruned'])} skipped={len(summary['skipped'])} "
          f"errors={len(summary['errors'])}  ->  {args.out}", file=sys.stderr)
    return 3 if (summary["errors"] or summary["incomplete"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
