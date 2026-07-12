"""
merge_judgments.py - Merge per-batch adjudication outputs (out_<prefix>_NNN.json,
written by the judge agents) into one judgments array, applying any adversarial
verifier downgrades (returned by the workflow) on the way.

Judgment schema (unified across the pipeline):
    {"source_fw": str, "source_ref": str, "target_fw": str, "target_ref": str,
     "relation": "EQUIVALENT|PARTIAL|SUPPORTS|INFORMS|no_relation",
     "confidence": float, "rationale": str}
(optional extra key: "conflict": bool, for intra-corpus contradictions).

Judgments are keyed on (source_fw, source_ref, target_fw, target_ref).
"no_relation" lines (case-insensitive) are dropped here - they're written
upstream purely so every candidate pair has an accounted-for verdict for
audit; gaps are computed at query time, never stored, so this is the one
place they get discarded.

Output feeds straight into build_edges.py (or, for a thin validate/persist
pass first, write_judgments.py).

Run:
    python crosswalk/merge_judgments.py --dir out/adjudication \
        --prefix impl --downgrades downgrades.json --out merged.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

# An adjudicating agent occasionally emits a full node id
# ("FRAMEWORK:version:ref") in a source_ref/target_ref field instead of just
# the bare native ref. Detect that shape (at least two colons) and repair it
# by stripping back to the ref - the part after the last colon.
_FULL_ID = re.compile(r"^[^:]+:[^:]+:.+$")


def repair_ref(ref: str) -> str:
    if isinstance(ref, str) and _FULL_ID.match(ref):
        return ref.rsplit(":", 1)[-1]
    return ref


def pair_key(j: dict) -> tuple[str, str, str, str]:
    return (j["source_fw"], j["source_ref"], j["target_fw"], j["target_ref"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge batch judgments + downgrades.")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--prefix", required=True, help="batch filename prefix, e.g. impl or dup")
    ap.add_argument("--downgrades", help="JSON array of {source_fw,source_ref,"
                     "target_fw,target_ref,new_relation,note}")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    d = Path(args.dir)
    batch_files = sorted(d.glob(f"out_{args.prefix}_*.json"))
    expected = sorted(d.glob(f"{args.prefix}_*.json"))
    missing = [b.name for b in expected
               if not (d / f"out_{b.name}").exists()]

    judgments = []
    for f in batch_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        judgments.extend(data["judgments"] if isinstance(data, dict) else data)

    # Repair bare-ref slips: a source_ref/target_ref that accidentally
    # carries a full node id instead of just the native ref.
    repaired = 0
    for j in judgments:
        for side in ("source_ref", "target_ref"):
            fixed = repair_ref(j[side])
            if fixed != j[side]:
                j[side] = fixed
                repaired += 1
    if repaired:
        print(f"Repaired {repaired} source_ref/target_ref field(s) that carried a full node id")

    seen = Counter(pair_key(j) for j in judgments)
    dups = sum(1 for c in seen.values() if c > 1)

    downgraded = 0
    if args.downgrades:
        dg = {pair_key(x): x
              for x in json.loads(Path(args.downgrades).read_text(encoding="utf-8"))}
        for j in judgments:
            x = dg.get(pair_key(j))
            if not x:
                continue
            changed = False
            if x.get("new_relation") and x["new_relation"] != j["relation"]:
                j["rationale"] = (f"{j['rationale']} [downgraded from {j['relation']} "
                                  f"by verifier: {x['note']}]")
                j["relation"] = x["new_relation"]
                j["confidence"] = min(j.get("confidence", 0.75), 0.75)
                changed = True
            if "new_conflict" in x and bool(j.get("conflict")) != bool(x["new_conflict"]):
                j["conflict"] = bool(x["new_conflict"])
                if not changed:
                    j["rationale"] = (f"{j['rationale']} [conflict flag revised "
                                      f"by verifier: {x['note']}]")
                changed = True
            downgraded += changed

    # Drop no_relation lines (case-insensitively) - written upstream for 1:1
    # pair accounting/audit, but they never become edges or stored facts.
    before = len(judgments)
    judgments = [j for j in judgments if j["relation"].strip().lower() != "no_relation"]
    dropped_no_relation = before - len(judgments)

    stats = Counter(j["relation"] for j in judgments)
    Path(args.out).write_text(json.dumps(judgments, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"Batches merged: {len(batch_files)} (missing outputs: {len(missing)})")
    if missing:
        for m in missing[:20]:
            print("  - no output for", m)
    print(f"Judgments: {len(judgments)} (duplicate pair lines: {dups}, "
          f"no_relation dropped: {dropped_no_relation})")
    print(f"Verifier downgrades applied: {downgraded}")
    print(f"By relation: {dict(stats.most_common())}")
    print(f"Wrote -> {args.out}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
