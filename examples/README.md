# 10-minute quickstart: ALPHA vs BETA

Two tiny, entirely synthetic frameworks — `examples/toy_alpha_nodes.json`
(access-control themed, refs `AC-1`..`AC-6`) and `examples/toy_beta_nodes.json`
(operations themed, refs `OPS-1`..`OPS-6`) — six requirements each, invented
for this walkthrough. Nothing here is a real standard, so there's no
licensing to worry about; it exists purely so you can run every pipeline
stage once, end to end, before pointing the kit at your own licensed
framework text.

Both files already have `canonical_intent` hand-written in the pipeline's
fixed `requires: X; in order to: Y.` shape, so this quickstart **skips stage
2 (Canonicalise)** — that's normally an LLM step, done here by hand so you
can get to Embed immediately. A couple of requirements are written to
genuinely overlap (`AC-6` "revoke a leaver's access within a day" and `OPS-2`
"remove a leaver's access by their last working day" say almost the same
thing) so that adjudication has something real to find, alongside pairs that
should come back `PARTIAL`, `INFORMS`, or nothing at all.

All commands below assume your current directory is the repo root, and that
you have a local Ollama running an embedding model (see the top-level
[`README`](../README.md#what-you-need) for the `CROSSWALK_OLLAMA_URL`
default) and a local Neo4j instance for the last step.

## 1. Embed

Embed `canonical_intent` for both files via Ollama. `--out` writes to a
scratch copy so the checked-in example files (which ship with `embedding:
null`, deliberately) stay untouched:

```sh
python crosswalk/embed.py --in examples/toy_alpha_nodes.json --out examples/out/toy_alpha_nodes.json
python crosswalk/embed.py --in examples/toy_beta_nodes.json --out examples/out/toy_beta_nodes.json
```

Add `--dry-run` first if you just want to see what would be sent, with no
requests made.

## 2. Candidates

Cosine-similarity candidate pairs across the two frameworks:

```sh
python crosswalk/candidates.py --in examples/out/toy_alpha_nodes.json --in examples/out/toy_beta_nodes.json --out examples/out/candidates.json
```

Twelve nodes is small enough that every plausible cross-framework pair
clears the default floor (`0.50`) — you should see a handful of candidates,
`AC-6 <-> OPS-2` scoring highest.

## 3. Prep adjudication batches

```sh
python crosswalk/prep_adjudication.py \
    --nodes examples/out/toy_alpha_nodes.json --nodes examples/out/toy_beta_nodes.json \
    --in examples/out/candidates.json --dir examples/out/adjudication
```

This writes `examples/out/adjudication/batch_001.json` — with this few
candidates you'll only ever get one batch, self-contained (refs, titles,
canonical intents, verbatim text, cosine score).

## 4. Adjudicate

Two equally valid ways to run this stage — "your own AI" from the top-level
README:

**Agent-driven** (a subscription coding agent, e.g. Claude Code, reading the
batch interactively) — see [`docs/AGENT_ADJUDICATION.md`](../docs/AGENT_ADJUDICATION.md)
for the exact prompt template. Point the agent at `crosswalk/RUBRIC.md` and
`examples/out/adjudication/batch_001.json`; it writes
`examples/out/adjudication/out_batch_001.json`.

**Direct API call** via [`crosswalk/api_adjudicator.py`](../crosswalk/api_adjudicator.py)
instead:

```sh
export ANTHROPIC_API_KEY=...
python crosswalk/api_adjudicator.py examples/out/adjudication/batch_001.json
```

Either path produces the same `out_batch_001.json` shape next to the batch
file.

## 5. Merge judgments and build edges

```sh
python crosswalk/merge_judgments.py --dir examples/out/adjudication --prefix batch --out examples/out/judgments.json
```

`build_edges.py` reads `merge_judgments.py`'s output directly (the unified
judgment schema), so there's no separate write/shard step needed for a
one-off run like this. (`write_judgments.py` exists for the other case —
an ongoing deployment where many adjudication rounds need to accumulate
side by side under `crosswalk/data/`, ready for `build_edges.py`'s default
`data/edges_*.jsonl` glob; skip it here.)

```sh
python crosswalk/build_edges.py \
    --nodes examples/out/toy_alpha_nodes.json --nodes examples/out/toy_beta_nodes.json \
    --judgments examples/out/judgments.json \
    --out examples/out/edges.json
```

## 6. Load Neo4j

Credentials come from the environment only — set these to your own local
instance, never on the command line:

```sh
export NEO4J_URI=neo4j://127.0.0.1:7687
export NEO4J_USERNAME=neo4j
export NEO4J_PASSWORD=...   # your own local password

python crosswalk/load_neo4j.py \
    --nodes examples/out/toy_alpha_nodes.json --nodes examples/out/toy_beta_nodes.json \
    --edges examples/out/edges.json
```

## 7. Query it

```cypher
MATCH (a:Node:ALPHA)-[r:EQUIVALENT|PARTIAL|SUPPORTS|INFORMS]-(b:Node:BETA)
RETURN a.native_ref AS alpha_ref, a.title AS alpha_title,
       type(r) AS relation, r.confidence AS confidence,
       b.native_ref AS beta_ref, b.title AS beta_title,
       r.rationale AS rationale
ORDER BY confidence DESC
```

If adjudication ran true to the rubric, `AC-6 <-> OPS-2` should come back
`EQUIVALENT` and a couple of the logging/change-control pairs (`AC-4 <->
OPS-1`, say) something weaker — `PARTIAL` or `INFORMS`. Whatever comes back,
you now have a small, real, queryable graph you built end to end — repeat
the same seven steps against your own licensed framework text and policy
documents, at whatever scale you need.

### Bonus: what has no match at all?

```cypher
MATCH (a:Node:ALPHA:Requirement)
WHERE NOT (a)-[:EQUIVALENT|PARTIAL|SUPPORTS|INFORMS]-()
RETURN a.native_ref, a.title
```

That's the gap query mentioned in the top-level README — a Cypher `WHERE
NOT` clause, not a stored fact, so it's always current with whatever's
actually in the graph.
