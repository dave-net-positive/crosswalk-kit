"""
build_edges.py - Step 5 assembly: expand adjudicated judgments into out/edges.json.

Adjudication itself is done by an LLM agent reasoning over each candidate pair's
canonical intents, and recorded as reviewable judgment lines:
    {source_fw, source_ref, target_fw, target_ref, relation, confidence, rationale}
Direction reads "source addresses/covers target". relation is one of
EQUIVALENT / PARTIAL / SUPPORTS / INFORMS (no_relation pairs are simply omitted;
gaps are computed at query time, never stored).

This generic builder validates the vocab, resolves node ids/titles, attaches the
cosine candidate_score, and writes out/edges.json. Framework version tags are
derived from the loaded nodes themselves (each node already carries its own
version), so no framework->version map needs to be hardcoded here.

Run:
    python crosswalk/build_edges.py --nodes out/framework_a_nodes.json \
        --nodes out/framework_b_nodes.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from models import load_nodes, make_id

HERE = Path(__file__).parent
RELATIONS = {"EQUIVALENT", "PARTIAL", "SUPPORTS", "INFORMS"}
DEFAULT_JUDGMENTS = sorted((HERE / "data").glob("edges_*.jsonl"))
DEFAULT_CANDIDATES = HERE / "out" / "candidates.json"
DEFAULT_OUT = HERE / "out" / "edges.json"


def load_judgments(path: Path) -> list[dict]:
    """Load one judgments file, accepting either shape: a JSONL shard (one
    judgment object per line, as written by write_judgments.py) or a single
    JSON document - a bare array or a {"judgments": [...]} dict - as written
    directly by merge_judgments.py's --out. Both shapes can start with '{',
    so the two are told apart by trying to parse the whole file as one JSON
    document first, falling back to line-by-line JSONL only if that fails."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return data["judgments"] if isinstance(data, dict) else data


def build_version_map(nodes: dict) -> tuple[dict[str, str], list[str]]:
    """Map framework -> version by inspecting the loaded nodes. Flags a problem
    if a framework's nodes disagree on version (should not happen in practice)."""
    versions: dict[str, set[str]] = {}
    for n in nodes.values():
        versions.setdefault(n.framework, set()).add(n.version)
    version_map, problems = {}, []
    for fw, vs in versions.items():
        if len(vs) > 1:
            problems.append(f"framework {fw!r} has multiple versions in the "
                             f"loaded node set: {sorted(vs)}")
        version_map[fw] = sorted(vs)[0]
    return version_map, problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Assemble adjudicated edges.")
    ap.add_argument("--judgments", action="append")
    ap.add_argument("--nodes", action="append", required=True,
                    help="node file (repeatable, at least one required)")
    ap.add_argument("--candidates", default=str(DEFAULT_CANDIDATES))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--method", default="embedding+llm-agent",
                    help="default method label for judgment lines that don't carry their own")
    ap.add_argument("--adjudicator", default="agent",
                    help="default adjudicator label for judgment lines that don't carry their own")
    args = ap.parse_args()

    judgment_paths = [Path(p) for p in (args.judgments or DEFAULT_JUDGMENTS)]
    node_paths = [Path(p) for p in args.nodes]

    nodes = {}
    for p in node_paths:
        for n in load_nodes(p):
            nodes[n.id] = n
    version_map, problems = build_version_map(nodes)

    def nid(fw: str, ref: str) -> str | None:
        if fw not in version_map:
            return None
        return make_id(fw, version_map[fw], ref)

    cand_score: dict[frozenset, float] = {}
    cpath = Path(args.candidates)
    if cpath.exists():
        for c in json.loads(cpath.read_text(encoding="utf-8")):
            cand_score[frozenset((c["a_id"], c["b_id"]))] = c["score"]

    edges = []
    for jp in judgment_paths:
        for j in load_judgments(jp):
            rel = j["relation"]
            if rel not in RELATIONS:
                problems.append(f"bad relation {rel!r} in {j}")
                continue
            sid = nid(j["source_fw"], j["source_ref"])
            tid = nid(j["target_fw"], j["target_ref"])
            for fw, x in ((j["source_fw"], sid), (j["target_fw"], tid)):
                if x is None:
                    problems.append(f"unknown framework or ref for {fw}: {j}")
                elif x not in nodes:
                    problems.append(f"unknown node id {x}")
            if sid is None or tid is None or sid not in nodes or tid not in nodes:
                continue
            edges.append({
                "source": sid,
                "source_framework": j["source_fw"],
                "source_ref": j["source_ref"],
                "source_title": nodes[sid].title,
                "target": tid,
                "target_framework": j["target_fw"],
                "target_ref": j["target_ref"],
                "target_title": nodes[tid].title,
                "relation": rel,
                "symmetric": rel == "EQUIVALENT",
                "confidence": j["confidence"],
                "candidate_score": cand_score.get(frozenset((sid, tid))),
                "rationale": j["rationale"],
                # Judgment lines may carry their own method/adjudicator (e.g. a
                # deterministic identity edge); default to the CLI-supplied labels.
                "method": j.get("method", args.method),
                "adjudicator": j.get("adjudicator", args.adjudicator),
                "conflict": j.get("conflict", False),
            })

    if problems:
        print("PROBLEMS:")
        for p in problems[:30]:
            print("  -", p)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    by_rel = {r: sum(1 for e in edges if e["relation"] == r) for r in sorted(RELATIONS)}
    meta = {
        "adjudicator": args.adjudicator,
        "method": args.method,
        "relation_vocab": sorted(RELATIONS),
        "direction": "source addresses/covers target",
        "edge_count": len(edges),
        "by_relation": by_rel,
    }
    out_path.write_text(json.dumps({"meta": meta, "edges": edges}, indent=2,
                                   ensure_ascii=False), encoding="utf-8")
    print(f"Edges: {len(edges)}  by relation: {by_rel}")
    print(f"Wrote -> {out_path}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
