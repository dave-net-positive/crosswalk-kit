"""
prep_adjudication.py - Slice candidate pairs into batch files for adjudication.

Filters out/candidates.json (optionally to just the pairs touching a given set
of frameworks, or to just cross-framework or just intra-framework pairs), and
writes numbered batch files to out/adjudication/. Each batch is self-contained
(refs, titles, canonical intents, cosine score, verbatim source text), so an
adjudicating agent needs nothing else in context.

Run:
    python crosswalk/prep_adjudication.py --nodes out/framework_a_nodes.json \
        --nodes out/framework_b_nodes.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_IN = HERE / "out" / "candidates.json"
DEFAULT_DIR = HERE / "out" / "adjudication"


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch candidate pairs for adjudication.")
    ap.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    ap.add_argument("--dir", dest="out_dir", default=str(DEFAULT_DIR))
    ap.add_argument("--batch", type=int, default=35)
    ap.add_argument("--nodes", dest="node_paths", action="append", required=True,
                    help="node file (repeatable) supplying verbatim raw_text for "
                         "each pair's a_text/b_text")
    ap.add_argument("--frameworks", nargs="*", default=None,
                    help="keep only pairs where either side's framework is one "
                         "of these (default: no framework filter, keep all)")
    ap.add_argument("--pair-class", choices=["any", "cross", "intra"],
                    default="any",
                    help="restrict to cross-framework pairs or same-framework "
                         "(intra) pairs - they may warrant different rubrics")
    ap.add_argument("--ref-prefix", default=None,
                    help="keep only pairs where one side's ref starts with this "
                         "(e.g. a newly added document's code) - incremental updates")
    ap.add_argument("--prefix", default="batch",
                    help="batch filename prefix (batch -> batch_001.json)")
    args = ap.parse_args()

    pairs = json.loads(Path(args.in_path).read_text(encoding="utf-8"))
    new = pairs
    if args.frameworks:
        keep = set(args.frameworks)
        new = [p for p in new if p["a_framework"] in keep or p["b_framework"] in keep]
    if args.pair_class == "cross":
        new = [p for p in new if p["a_framework"] != p["b_framework"]]
    elif args.pair_class == "intra":
        new = [p for p in new if p["a_framework"] == p["b_framework"]]
    if args.ref_prefix:
        new = [p for p in new if p["a_ref"].startswith(args.ref_prefix)
               or p["b_ref"].startswith(args.ref_prefix)]

    # Ground each side in its verbatim source text (truncated), so adjudicating
    # agents judge the actual wording, not only the canonical intent.
    from models import load_nodes
    raw = {}
    for npath in args.node_paths:
        p = Path(npath)
        if p.exists():
            for node in load_nodes(p):
                raw[node.id] = node.raw_text
    for p in new:
        for side in ("a", "b"):
            t = raw.get(p[f"{side}_id"], "")
            p[f"{side}_text"] = (t[:600] + " [...]") if len(t) > 600 else t

    combos = Counter(tuple(sorted((p["a_framework"], p["b_framework"]))) for p in new)
    print(f"Candidate pairs: {len(pairs)} total, {len(new)} selected")
    for (x, y), c in combos.most_common():
        print(f"  {x} <-> {y}: {c}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{args.prefix}_*.json"):
        old.unlink()
    batches = [new[i:i + args.batch] for i in range(0, len(new), args.batch)]
    for i, b in enumerate(batches, 1):
        (out_dir / f"{args.prefix}_{i:03d}.json").write_text(
            json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(batches)} batch files (<= {args.batch} pairs, "
          f"prefix {args.prefix!r}) -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
