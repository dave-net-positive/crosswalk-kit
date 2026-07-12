"""
validate_embedder.py - Measure how well an alternative embedding space
regenerates the ADJUDICATED edges as candidates. This is the safety gate for
switching embedders: false negatives at candidate stage are unrecoverable, and
the adjudicated edge set is ground truth.

Method: for every stored pipeline edge (identity/deterministic edges excluded -
they weren't embedding-derived), check whether the pair would surface under the
candidates.py regime in the new space: target in source's top-k of the relevant
pool, or vice versa (pairs were generated symmetrically). Also reports the
true-pair cosine distribution so a floor can be chosen for the new space rather
than assuming the old model's floors.

Node files are supplied via repeatable --nodes; --suffix is inserted before the
extension on each supplied path to load the embedding variant under test, e.g.
--nodes out/framework_a_nodes.json --suffix _e06 loads
out/framework_a_nodes_e06.json.

Run:
    python crosswalk/validate_embedder.py --nodes out/framework_a_nodes.json \
        --nodes out/framework_b_nodes.json --suffix _e06
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from models import load_nodes

HERE = Path(__file__).parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", dest="node_paths", action="append", required=True,
                    help="base node file (repeatable); --suffix is applied to "
                         "each to load the embedding variant under test")
    ap.add_argument("--suffix", default="_e06",
                    help="suffix inserted before .json on each --nodes path")
    ap.add_argument("--k-cross", type=int, default=8)
    ap.add_argument("--k-intra", type=int, default=6)
    ap.add_argument("--intra", action="append", default=None,
                    help="framework code(s) that also get an intra-framework "
                         "pool (same semantics as candidates.py --intra); repeatable")
    ap.add_argument("--edges", default=str(HERE / "out" / "edges.json"))
    args = ap.parse_args()

    intra_set = set(args.intra or [])

    nodes = []
    for p in args.node_paths:
        p = Path(p)
        variant = p.with_name(p.stem + args.suffix + p.suffix)
        nodes.extend(load_nodes(variant))
    emb = {n.id: n for n in nodes if n.is_mappable() and n.embedding}
    ids = list(emb)
    idx = {nid: i for i, nid in enumerate(ids)}
    V = np.array([emb[nid].embedding for nid in ids], dtype=np.float64)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    S = V @ V.T
    fw = np.array([emb[nid].framework for nid in ids])
    doc = np.array([(emb[nid].extra or {}).get("doc") or "" for nid in ids])

    # Rank matrices per node against its pool(s) (mask then argsort once).
    def topk_sets(k_cross: int, k_intra: int) -> list[set[int]]:
        out = []
        for i in range(len(ids)):
            cross = np.where(fw != fw[i])[0]
            if fw[i] in intra_set:
                intra = np.where((fw == fw[i]) & (doc != doc[i]))[0]
                intra_top = intra[np.argsort(S[i, intra])[::-1][:k_intra]]
                cross_top = cross[np.argsort(S[i, cross])[::-1][:k_cross]]
                out.append(set(intra_top) | set(cross_top))
            else:
                out.append(set(cross[np.argsort(S[i, cross])[::-1][:k_cross]]))
        return out

    tops = topk_sets(args.k_cross, args.k_intra)

    edges = json.loads(Path(args.edges).read_text(encoding="utf-8"))["edges"]
    truth = [e for e in edges
             if not str(e.get("method", "")).startswith("identity")
             and e["source"] in idx and e["target"] in idx]

    hits, sims = defaultdict(int), []
    total = defaultdict(int)
    for e in truth:
        a, b = idx[e["source"]], idx[e["target"]]
        rel = e["relation"]
        total[rel] += 1
        sims.append(S[a, b])
        if b in tops[a] or a in tops[b]:
            hits[rel] += 1

    sims = np.array(sims)
    print(f"Ground-truth pipeline edges evaluable: {len(truth)}")
    overall_h, overall_t = sum(hits.values()), sum(total.values())
    for rel in sorted(total):
        print(f"  {rel:<10} recall@pools: {hits[rel]}/{total[rel]} "
              f"({100*hits[rel]/total[rel]:.1f}%)")
    print(f"  OVERALL    {overall_h}/{overall_t} ({100*overall_h/overall_t:.1f}%)")
    print(f"\nTrue-pair cosine distribution in this space:")
    for p in (1, 5, 10, 25, 50):
        print(f"  p{p:<3} {np.percentile(sims, p):.3f}")
    print(f"\nSuggested floors for this space: cross ~= p5 "
          f"({np.percentile(sims, 5):.2f}), keep recall-generous.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
