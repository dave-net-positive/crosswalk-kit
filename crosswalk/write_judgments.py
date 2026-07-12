"""
write_judgments.py - Thin validate/normalise + persist step: take a merged
judgments array (unified schema, as produced by merge_judgments.py) and
write it out sharded into per-framework-pair JSONL files under data/, so
repeated adjudication rounds accumulate there without clobbering earlier
ones, and are all picked up by build_edges.py's default data/edges_*.jsonl
glob.

For a one-off run (see examples/README.md) you don't need this step at all
- hand merge_judgments.py's --out file straight to build_edges.py's
--judgments flag, which reads the unified schema directly. Reach for
write_judgments.py when you want this round's edges to persist to disk
under data/ alongside previous rounds, which is the normal mode for an
ongoing internal deployment with many adjudication rounds over time.

Judgment schema (unified across the pipeline):
    {"source_fw": str, "source_ref": str, "target_fw": str, "target_ref": str,
     "relation": "EQUIVALENT|PARTIAL|SUPPORTS|INFORMS|no_relation",
     "confidence": float, "rationale": str}
(optional extra key: "conflict": bool). "no_relation" lines (matched
case-insensitively) are dropped here - gaps are computed at query time,
never stored.

Output files are named data/edges_<fwa>_<fwb><suffix>.jsonl (alphabetical,
lower-case) so repeated adjudication rounds sit beside - and never clobber
- earlier ones.

Run:
    python crosswalk/write_judgments.py --in judgments.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
ADJUDICATOR = "agent (adjudication round; EQUIVALENTs adversarially verified)"
RELATIONS = {"EQUIVALENT", "PARTIAL", "SUPPORTS", "INFORMS"}
REQUIRED_FIELDS = ("source_fw", "source_ref", "target_fw", "target_ref",
                    "relation", "confidence", "rationale")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Validate a merged judgments array and shard it into data/*.jsonl.")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--suffix", default="_r2")
    ap.add_argument("--adjudicator", default=ADJUDICATOR)
    args = ap.parse_args()

    data = json.loads(Path(args.in_path).read_text(encoding="utf-8"))
    judgments = data["judgments"] if isinstance(data, dict) else data

    by_pair: dict[tuple[str, str], list[str]] = {}
    stats = Counter()
    problems = []
    no_relation = 0
    for j in judgments:
        rel = str(j.get("relation", ""))
        stats[rel] += 1
        if rel.strip().lower() == "no_relation":
            no_relation += 1
            continue
        missing = [f for f in REQUIRED_FIELDS if f not in j]
        if missing:
            problems.append(f"missing {missing}: {j}")
            continue
        if rel not in RELATIONS:
            problems.append(f"bad relation {rel!r}: {j}")
            continue
        rec = {
            "source_fw": j["source_fw"], "source_ref": j["source_ref"],
            "target_fw": j["target_fw"], "target_ref": j["target_ref"],
            "relation": rel, "confidence": j["confidence"],
            "adjudicator": j.get("adjudicator", args.adjudicator),
            "rationale": j["rationale"],
        }
        if j.get("conflict"):
            rec["conflict"] = True
        line = json.dumps(rec, ensure_ascii=False)
        by_pair.setdefault(tuple(sorted((j["source_fw"], j["target_fw"]))), []).append(line)

    for (fwa, fwb), lines in sorted(by_pair.items()):
        out = HERE / "data" / f"edges_{fwa.lower()}_{fwb.lower()}{args.suffix}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{out.name}: {len(lines)} edges")

    print(f"\nJudgments: {sum(stats.values())}  by relation: {dict(stats)}")
    print(f"Stored: {sum(len(v) for v in by_pair.values())} "
          f"(no_relation dropped: {no_relation})")
    if problems:
        print("PROBLEMS:")
        for p in problems[:10]:
            print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
