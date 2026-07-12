"""
load_neo4j.py - Step 6: load nodes and adjudicated edges into Neo4j.

Loads framework nodes and the typed crosswalk edges from out/edges.json into
Neo4j. Idempotent: MERGE on node id and on (source)-[rel]->(target), so re-runs
and added frameworks update in place.

Modelling:
  - (:Node:<Framework>:<NodeType>) with id/framework/version/native_ref/node_type/
    title/raw_text/canonical_intent. Optional `embedding` with --with-embeddings.
  - (parent)-[:CONTAINS]->(child) from parent_id (e.g. objective->principle->outcome).
  - (source)-[:EQUIVALENT|PARTIAL|SUPPORTS|INFORMS]->(target) with confidence,
    rationale, candidate_score, method, adjudicator. Gaps are NOT stored - they are
    computed at query time as the absence of an incoming coverage edge.

Credentials come from the environment only (never disk/args): NEO4J_URI,
NEO4J_USERNAME (or NEO4J_USER), NEO4J_PASSWORD.

Run:
    python crosswalk/load_neo4j.py --nodes out/framework_a_nodes.json \
        --nodes out/framework_b_nodes.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from models import load_nodes

HERE = Path(__file__).parent
DEFAULT_EDGES = HERE / "out" / "edges.json"
RELATIONS = {"EQUIVALENT", "PARTIAL", "SUPPORTS", "INFORMS"}
_LABEL = re.compile(r"[^A-Za-z0-9_]")


def env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def label(s: str) -> str:
    return _LABEL.sub("", s)


def node_type_label(node_type: str) -> str:
    return label(node_type.capitalize())


def main() -> int:
    ap = argparse.ArgumentParser(description="Load crosswalk into Neo4j.")
    ap.add_argument("--nodes", action="append", required=True,
                    help="node file (repeatable, at least one required)")
    ap.add_argument("--edges", default=str(DEFAULT_EDGES))
    ap.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    ap.add_argument("--with-embeddings", action="store_true",
                    help="also store the embedding vector on each node")
    ap.add_argument("--wipe", action="store_true",
                    help="delete all existing nodes/edges first (destructive)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    node_paths = [Path(p) for p in args.nodes]
    nodes = []
    for p in node_paths:
        nodes.extend(load_nodes(p))
    edges = json.loads(Path(args.edges).read_text(encoding="utf-8"))["edges"]
    parents = [(n.parent_id, n.id) for n in nodes if n.parent_id]

    print(f"Nodes: {len(nodes)}  CONTAINS: {len(parents)}  edges: {len(edges)}")
    by_rel = {r: sum(1 for e in edges if e["relation"] == r) for r in sorted(RELATIONS)}
    print(f"  edges by relation: {by_rel}")

    uri = env("NEO4J_URI", default="neo4j://127.0.0.1:7687")
    user = env("NEO4J_USERNAME", "NEO4J_USER", default="neo4j")
    password = env("NEO4J_PASSWORD")
    print(f"Endpoint: {uri}  user: {user}  password: "
          f"{'set' if password else 'MISSING'}  database: {args.database}")

    if args.dry_run:
        print("\nDry run: no connection made.")
        return 0
    if not password:
        print("\nNo NEO4J_PASSWORD in env. Set NEO4J_URI/NEO4J_USERNAME/"
              "NEO4J_PASSWORD and re-run. Nothing written.")
        return 2

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - surface connection errors plainly
        print(f"Cannot connect to Neo4j at {uri}: {exc}")
        return 2

    with driver.session(database=args.database) as s:
        if args.wipe:
            s.run("MATCH (n) DETACH DELETE n")
            print("Wiped existing graph.")

        s.run("CREATE CONSTRAINT node_id IF NOT EXISTS "
              "FOR (n:Node) REQUIRE n.id IS UNIQUE")
        s.run("CREATE INDEX node_framework IF NOT EXISTS FOR (n:Node) ON (n.framework)")
        s.run("CREATE INDEX node_type IF NOT EXISTS FOR (n:Node) ON (n.node_type)")

        for n in nodes:
            props = {
                "id": n.id, "framework": n.framework, "version": n.version,
                "native_ref": n.native_ref, "node_type": n.node_type,
                "title": n.title, "raw_text": n.raw_text,
                "canonical_intent": n.canonical_intent,
            }
            # Flatten adapter metadata (extra) into scalar node properties -
            # claimed_type/actual_mix/doc_class/strength/section etc. are core
            # query targets ("titles lie" analysis). Skip non-scalar values.
            for k, v in (n.extra or {}).items():
                if v is not None and isinstance(v, (str, int, float, bool)) \
                        and k not in props:
                    props[k] = v
            if args.with_embeddings and n.embedding:
                props["embedding"] = n.embedding
            lbls = f"{label(n.framework)}:{node_type_label(n.node_type)}"
            s.run(f"MERGE (n:Node {{id:$id}}) SET n += $props SET n:{lbls}",
                  id=n.id, props=props)

        for parent_id, child_id in parents:
            s.run("MATCH (p:Node {id:$p}), (c:Node {id:$c}) "
                  "MERGE (p)-[:CONTAINS]->(c)", p=parent_id, c=child_id)

        for e in edges:
            rel = e["relation"]
            if rel not in RELATIONS:
                continue
            s.run(
                f"MATCH (a:Node {{id:$sid}}), (b:Node {{id:$tid}}) "
                f"MERGE (a)-[r:{rel}]->(b) SET r += $props",
                sid=e["source"], tid=e["target"],
                props={
                    "confidence": e["confidence"],
                    "rationale": e["rationale"],
                    "candidate_score": e.get("candidate_score"),
                    "symmetric": e.get("symmetric", False),
                    "method": e.get("method"),
                    "adjudicator": e.get("adjudicator"),
                    # Useful when crosswalking mandates from the same corpus:
                    # the two sides contradict each other (e.g. different
                    # retention periods) rather than merely overlapping.
                    "conflict": e.get("conflict", False),
                },
            )

        counts = s.run(
            "MATCH (n:Node) WITH count(n) AS nodes "
            "MATCH ()-[r]->() RETURN nodes, count(r) AS rels"
        ).single()
        print(f"\nLoaded. Graph now has {counts['nodes']} nodes and "
              f"{counts['rels']} relationships.")
    driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
