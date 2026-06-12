"""Apply agent classification verdicts to a graph (deterministic write-back).

    python scripts/classify_apply.py knowledge-base/graph.json verdicts.json \
        -o knowledge-base/graph.json

`verdicts.json` is produced by the agent's read-cold classification — a JSON list of
``{page_id, process_type?, topics?, documents:[{target, confidence?, evidence?}]}``.
This writes ``documents`` edges (``via:"agent"``) + page attrs; ids not in the graph are
skipped and reported. No LLM here — the judgment already happened in the agent.
"""
from __future__ import annotations

import argparse
import json
import sys

import sys

# allow running straight from a checkout/unpack — no install needed
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from graphbuilder import load_graph, save_graph
from graphbuilder.confluence import apply_classifications


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="classify_apply", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("graph", help="graph JSON to augment (e.g. knowledge-base/graph.json)")
    ap.add_argument("verdicts", help="agent verdicts JSON (a list of verdict objects)")
    ap.add_argument("-o", "--out", default=None, help="output graph JSON (default: overwrite input)")
    args = ap.parse_args(argv)

    graph = load_graph(args.graph)
    try:
        with open(args.verdicts, encoding="utf-8") as fh:
            verdicts = json.load(fh)
    except Exception as exc:
        print(f"error: cannot read verdicts: {exc}", file=sys.stderr)
        return 2
    if not isinstance(verdicts, list):
        print("error: verdicts JSON must be a list of verdict objects", file=sys.stderr)
        return 2

    g, report = apply_classifications(graph, verdicts)
    save_graph(g, args.out or args.graph)
    print(f"applied={report['applied']} updated_pages={report['updated_pages']} "
          f"skipped={len(report['skipped'])}  ->  {args.out or args.graph}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
