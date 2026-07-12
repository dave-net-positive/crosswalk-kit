"""
candidates.py - Build step 4: generate candidate cross-framework pairs by cosine
similarity over the node embeddings, to be adjudicated by an LLM agent in step 5.

Purpose: cut the all-pairs comparison down to a tractable set. Embeddings are
recall-tuned candidate generation, not the final answer - false positives are
cheap (adjudication prunes them), false negatives are expensive (they never get
a second look), so err generous.

Method:
  - Load mappable, embedded nodes from one or more framework node files.
  - For each node, take its top-k nearest neighbours in OTHER frameworks by cosine
    (same-framework pairs are blocked in this cross-framework pool).
  - Optionally, for framework(s) named with --intra (e.g. a large internal policy
    corpus with many documents), also take top-k nearest neighbours within the
    SAME framework, blocking same-document pairs via node.extra["doc"] - this
    surfaces cross-document duplicate/overlapping mandates without crowding out
    the cross-framework matches.
  - Apply a floor threshold to drop weak pairs.
  - Deduplicate symmetric pairs, keeping the highest score.

Pure stdlib fallback if numpy is unavailable; numpy is used when present for an
exact one-matrix-product cosine over the whole corpus.

Run:
    python crosswalk/candidates.py --in out/framework_a_nodes.json \
        --in out/framework_b_nodes.json
    python crosswalk/candidates.py --in out/framework_a_nodes.json \
        --in out/internal_nodes.json --intra INTERNAL
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from models import Node, load_nodes

HERE = Path(__file__).parent
DEFAULT_OUT = HERE / "out" / "candidates.json"


def normalise(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def main() -> int:
    ap = argparse.ArgumentParser(description="Cosine candidate generation.")
    ap.add_argument("--in", dest="in_paths", action="append", required=True,
                    help="node file (repeatable, at least one required)")
    ap.add_argument("--out", dest="out_path", default=str(DEFAULT_OUT))
    ap.add_argument("--k", type=int, default=8, help="top-k neighbours per node")
    ap.add_argument("--floor", type=float, default=0.50, help="min cosine to keep")
    ap.add_argument("--intra", action="append", default=None,
                    help="framework code to also generate a same-framework "
                         "(intra) candidate pool for, blocking same-document "
                         "pairs via node.extra['doc']; repeatable")
    ap.add_argument("--intra-k", type=int, default=6,
                    help="top-k for intra-framework pairs")
    ap.add_argument("--intra-floor", type=float, default=0.58,
                    help="min cosine for intra-framework pairs (units from the "
                         "same framework/corpus share an authoring style, so the "
                         "similarity baseline is higher)")
    args = ap.parse_args()

    in_paths = [Path(p) for p in args.in_paths]
    out_path = Path(args.out_path)
    intra_set = set(args.intra or [])

    nodes: list[Node] = []
    for p in in_paths:
        nodes.extend(load_nodes(p))

    targets = [n for n in nodes if n.is_mappable() and n.embedding]
    by_fw: dict[str, list[Node]] = {}
    vecs: dict[str, list[float]] = {}
    for n in targets:
        by_fw.setdefault(n.framework, []).append(n)
        vecs[n.id] = normalise(n.embedding)

    frameworks = sorted(by_fw)
    print(f"Embedded mappable nodes: {len(targets)}")
    for fw in frameworks:
        print(f"  {fw}: {len(by_fw[fw])}")
    if len(frameworks) < 2 and not intra_set:
        print("\nOnly one framework present and no --intra requested; "
              "no candidate pairs to generate.")
        return 0

    # For each node, its top-k neighbours in other frameworks (above the floor).
    # Frameworks named in --intra additionally get their own top-k among OTHER
    # documents' units within the same framework - a separate pool with its own
    # k/floor. Same-document pairs are always blocked in that pool.
    best: dict[frozenset, dict] = {}

    def record(n: Node, m: Node, score: float) -> None:
        key = frozenset((n.id, m.id))
        if key not in best or score > best[key]["score"]:
            best[key] = {
                "score": round(score, 4),
                "a_id": n.id, "a_framework": n.framework,
                "a_ref": n.native_ref, "a_title": n.title,
                "a_canonical_intent": n.canonical_intent,
                "b_id": m.id, "b_framework": m.framework,
                "b_ref": m.native_ref, "b_title": m.title,
                "b_canonical_intent": m.canonical_intent,
            }

    try:
        import numpy as np
    except ImportError:
        np = None

    if np is not None:
        # Exact cosine via one matrix product (vectors are pre-normalised).
        order = targets
        idx = {n.id: i for i, n in enumerate(order)}
        V = np.array([vecs[n.id] for n in order], dtype=np.float64)
        S = V @ V.T

        def take(n: Node, pool: list[Node], k: int, floor: float) -> None:
            if not pool:
                return
            cols = np.fromiter((idx[m.id] for m in pool), dtype=np.int64)
            row = S[idx[n.id], cols]
            top = np.argsort(row)[::-1][:k]
            for j in top:
                score = float(row[j])
                if score < floor:
                    break
                record(n, pool[int(j)], score)
    else:
        def take(n: Node, pool: list[Node], k: int, floor: float) -> None:
            scored = sorted(((dot(vecs[n.id], vecs[m.id]), m) for m in pool),
                            key=lambda t: t[0], reverse=True)
            for score, m in scored[:k]:
                if score < floor:
                    break
                record(n, m, score)

    for fw in frameworks:
        cross = [m for ofw in frameworks if ofw != fw for m in by_fw[ofw]]
        for n in by_fw[fw]:
            take(n, cross, args.k, args.floor)
            if fw in intra_set:
                doc = n.extra.get("doc")
                intra = [m for m in by_fw[fw] if m.extra.get("doc") != doc]
                take(n, intra, args.intra_k, args.intra_floor)

    pairs = sorted(best.values(), key=lambda d: d["score"], reverse=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(pairs, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nCandidate pairs (k={args.k}, floor={args.floor}): {len(pairs)}")
    if pairs:
        scores = [p["score"] for p in pairs]
        print(f"  score range: {min(scores):.3f} - {max(scores):.3f}")
        nodes_with = len({i for p in pairs for i in (p["a_id"], p["b_id"])})
        print(f"  nodes appearing in >=1 pair: {nodes_with}/{len(targets)}")
        print("\nTop 12 candidates:")
        for p in pairs[:12]:
            print(f"  {p['score']:.3f}  {p['a_framework']} {p['a_ref']} ({p['a_title']})"
                  f"  <->  {p['b_framework']} {p['b_ref']} ({p['b_title']})")
    print(f"\nWrote -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
