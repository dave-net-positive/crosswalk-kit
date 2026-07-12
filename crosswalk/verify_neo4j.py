"""
verify_neo4j.py - Sanity queries against the loaded crosswalk graph.

Confirms the load and demonstrates that gaps are computed at query time (the
absence of a coverage edge), not stored. Generic over whatever frameworks and
node types are actually in the graph: node/relationship counts are grouped
dynamically, and the "uncovered targets" gap query defaults to any mappable
node type (see models.MAPPABLE_TYPES) across all frameworks, narrowable with
--framework/--node-type. Credentials from env (see load_neo4j.py).

Run:
    python crosswalk/verify_neo4j.py
    python crosswalk/verify_neo4j.py --framework CAF --node-type outcome
"""

from __future__ import annotations

import argparse
import os

from load_neo4j import env
from models import MAPPABLE_TYPES

COVER = ":EQUIVALENT|PARTIAL|SUPPORTS|INFORMS"


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the loaded crosswalk graph.")
    ap.add_argument("--framework", default=None,
                    help="restrict the gap query to one framework (default: all)")
    ap.add_argument("--node-type", default=None,
                    help="restrict the gap query to one node type "
                         "(default: any type in models.MAPPABLE_TYPES)")
    ap.add_argument("--limit", type=int, default=40,
                    help="max uncovered rows to print")
    args = ap.parse_args()

    uri = env("NEO4J_URI", default="neo4j://127.0.0.1:7687")
    user = env("NEO4J_USERNAME", "NEO4J_USER", default="neo4j")
    password = env("NEO4J_PASSWORD")
    if not password:
        print("No NEO4J_PASSWORD in env.")
        return 2

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(user, password))
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    with driver.session(database=db) as s:
        print("Nodes by framework / type:")
        for r in s.run("MATCH (n:Node) RETURN n.framework AS fw, n.node_type AS t, "
                       "count(*) AS c ORDER BY fw, t"):
            print(f"  {r['fw']:9s} {r['t']:10s} {r['c']}")

        print("\nRelationships by type:")
        for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c "
                       "ORDER BY c DESC"):
            print(f"  {r['t']:12s} {r['c']}")

        conditions = []
        params: dict = {"limit": args.limit}
        if args.framework:
            conditions.append("c.framework = $fw")
            params["fw"] = args.framework
        if args.node_type:
            conditions.append("c.node_type = $nt")
            params["nt"] = args.node_type
        else:
            conditions.append("c.node_type IN $mappable")
            params["mappable"] = sorted(MAPPABLE_TYPES)
        where_clause = " AND ".join(conditions)

        print("\nGAP - mappable nodes with no coverage from any framework (live):")
        rows = list(s.run(
            f"MATCH (c:Node) WHERE {where_clause} AND NOT (c)-[{COVER}]-(:Node) "
            f"RETURN c.framework AS fw, c.node_type AS nt, c.native_ref AS ref, "
            f"c.title AS title ORDER BY fw, nt, ref LIMIT $limit", **params))
        for r in rows:
            print(f"  {r['fw']:10s} {r['nt']:14s} {r['ref']:10s} {r['title']}")
        print(f"  ({len(rows)} shown, limit {args.limit})")

        print("\nTop EQUIVALENT edges:")
        for r in s.run(
            "MATCH (a:Node)-[r:EQUIVALENT]->(b:Node) "
            "RETURN a.framework AS af, a.native_ref AS ar, b.framework AS bf, "
            "b.native_ref AS br, r.confidence AS conf "
            "ORDER BY conf DESC LIMIT 6"):
            print(f"  {r['conf']}  {r['af']} {r['ar']} == {r['bf']} {r['br']}")
    driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
