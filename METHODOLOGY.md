# Methodology

This is the reasoning behind crosswalk-kit's pipeline: why the graph is
shaped the way it is, why the relation vocabulary is deliberately narrow,
and the accuracy engineering that makes an LLM-adjudicated crosswalk
trustworthy enough to act on. `crosswalk/RUBRIC.md` is the operational
distillation of the relation section below — hand it to an LLM directly.
`docs/AGENT_ADJUDICATION.md` covers running the LLM-driven steps with a
subscription coding agent instead of API calls.

## 1. Why a graph, not a spreadsheet

The standard compliance-crosswalk artefact is a spreadsheet: one column per
framework, one row per requirement, cells marked "covers" / "partial" /
blank. It has three structural problems that don't show up until the
spreadsheet is a few hundred rows old:

- **No rationale.** A cell says "partial". Partial *how*? Which words in
  which document justified that judgement? Six months later nobody can
  reconstruct it, so nobody trusts it enough to challenge it.
- **No queries.** "Which ISO 27002 controls have no internal policy behind
  them" is a manual VLOOKUP exercise, redone by hand every time someone
  asks, and never quite the same way twice.
- **Gaps go stale.** A gap is really an absence — a node with no
  qualifying edge. In a spreadsheet, absence is whatever's left after
  someone finished filling it in; it doesn't know it's a gap, and it
  doesn't update itself when a framework revises a clause number or a
  policy gets rewritten.

A typed graph fixes all three at once. Every requirement is a node;
every asserted relationship is an edge carrying a machine- and
human-readable rationale; and a gap is not a stored fact at all — it is
simply the result of a Cypher query for nodes with no adjudicated edge of
the relevant type. Run the query again after adding a framework or a
policy revision and the gap list is current, because it was never stored
as anything other than "what falls out of the graph right now".

None of this requires the graph to be bigger or cleverer than a
spreadsheet — it requires the crosswalk to be represented as first-class,
typed, directed relationships between first-class, typed nodes, instead of
a grid of strings.

## 2. The node model

### Units, not documents

The unit of mapping is never a whole document, clause list, or control
family — it is the smallest requirement-bearing passage that can carry its
own intent. A framework document is a *container* of nodes; a clause with
five numbered sub-requirements is five nodes, not one; a control that
bundles "define a policy AND review it annually" is arguably two nodes if
the two halves are ever going to be satisfied by different things.
Granularity mismatches are the single biggest source of false "no
relation" verdicts and false disagreement with external references (see
§7), so the parsing step (`crosswalk/adapters/`) is where accuracy is won
or lost, before any embedding or LLM adjudication ever runs.

Not every node is a candidate for mapping. Structural nodes — objectives,
principles, whole documents, section headers — exist to be *containers*
(linked to their children by a hierarchy edge) but carry no independent
requirement of their own, so they never enter candidate generation or
adjudication. Only leaf, requirement-bearing nodes are "mappable": they
have raw text that asserts something, and therefore a `canonical_intent`
that can be embedded and compared. A node file marks this with an
`is_mappable()`-style check (structural type vs. leaf type), not a
separate list to keep in sync by hand.

### Typed units

`node_type` is a per-framework vocabulary, not one universal enum, because
frameworks structure requirements differently: a `control` (ISO-style,
imperative — "the organisation shall..."), a `clause` (management-system
structure — numbered, often procedural), an `outcome` (goal-stated — "user
access is reviewed at appropriate intervals" rather than "you shall review
access"), and, once you extend the kit to your own material, a
`policy_statement`, `procedure`, `standard`, or `process` decomposed out of
a real document (§8). The schema doesn't force these into one shape;
`extra` is where framework-specific properties live (an internal
statement's obligation strength — MUST / SHOULD / INFO — a control
family code, a management-system clause number), so the stable columns
(`id`, `framework`, `version`, `native_ref`, `node_type`, `title`,
`raw_text`, `canonical_intent`, `parent_id`, `extra`, `embedding`,
`embedding_model`, `embedding_dim`) never have to change shape to
accommodate a new framework's quirks.

### Canonicalising intent

Two requirements can mean the same thing and read nothing alike. An
ISO-style control says "the organisation shall implement logging of user
activities, exceptions and security events". A CAF-style outcome says
"you have identified and can readily distinguish permitted activity from
that which should be prevented". A well-written internal procedure says
"IT will retain audit trail exports for a minimum of twelve months in case
an investigation is required later." All three are, at the level of
intent, close enough to be worth a look — but a cosine embedding over the
*raw text* will separate them, because raw text carries idiom: imperative
legal-standard phrasing, outcome-stated goal phrasing, and hedged internal
policy prose occupy different regions of embedding space even when the
underlying requirement is identical.

The canonicalisation step (LLM, one call per node or small batch) exists
to strip that idiom before anything gets vectorised. Every mappable node's
`canonical_intent` is written to a fixed two-part shape:

```
requires: <the actual obligation, stated plainly>; in order to: <the purpose it serves>.
```

The `requires:` clause pulls the prescriptive and the outcome-stated
versions of the same requirement into the same wording register — "shall
implement logging" and "can distinguish permitted from prohibited
activity" both canonicalise toward something like "requires: security-
relevant user activity is logged and reviewable" and now sit close
together in embedding space. The `in order to:` clause matters even when
the source text doesn't state a purpose explicitly: forcing the model to
infer the plausible purpose keeps the two-part shape uniform across the
whole corpus, and a stable purpose clause turns out to be a strong
semantic anchor — two requirements that serve visibly different purposes
are rarely worth an EQUIVALENT or PARTIAL edge even if their obligation
clauses look similar, which the embedding now surfaces automatically
instead of relying on the adjudicator to catch it by inspection every
time.

`canonical_intent` is a normalisation for *comparison*, not a paraphrase
for *display* — the adjudication step (§4, §5) is always given the
verbatim `raw_text` alongside it, precisely so nuance the normalisation
smoothed away isn't lost at the point a judgement actually gets made.

## 3. The relation vocabulary

Four typed relations, plus an implicit fifth outcome that never becomes an
edge:

| Relation | Meaning | Decision test |
|---|---|---|
| `EQUIVALENT` | Source and target require substantively the same thing, at the same scope. | Would satisfying one, in full, satisfy the other, in full? |
| `PARTIAL` | Source addresses only part of target's scope, or vice versa. | Does satisfying source leave a meaningful piece of target's requirement unaddressed (or vice versa)? |
| `SUPPORTS` | Source is a precondition that makes target achievable, without itself being the thing that satisfies target. | Does target's requirement become impossible, or much weaker, without source in place — even though source doesn't itself do what target asks? |
| `INFORMS` | Source supplies context, definitions, or scope-narrowing relevant to interpreting target, with no obligation overlap. | Would you cite source in a footnote explaining target, without claiming source *does* any part of what target requires? |
| *(dropped)* | No defensible relationship. | If none of the above tests pass, the pair is not written as an edge at all — it is not "no_relation", it simply doesn't exist in the graph. |

**Direction.** Every typed edge is directed: `source → target` means
*source addresses or enables target*. For `EQUIVALENT` the relationship is
also recorded as symmetric (satisfying either side satisfies the other),
but the edge is still stored with a direction for consistency; for
`PARTIAL`, `SUPPORTS`, and `INFORMS`, direction is load-bearing and must
not be assigned arbitrarily — reversing it changes the claim from "this
covers part of that" to "that covers part of this".

**`SUPPORTS` is enablement, not coverage.** This is the relation most
prone to inflation, because almost everything "supports" almost everything
else at some level of abstraction. Reserve it for a genuine dependency:
the target's requirement is not fully meaningful, or not achievable, until
the source is in place. "Unique, attributable user accounts" `SUPPORTS`
"security events are attributed to an individual" — you cannot satisfy the
target at all without the source. That is a `SUPPORTS` edge. "Staff
training" does not `SUPPORTS` every control that mentions people, just
because trained people do things better in general — that is not a
relation, it is a background assumption, and background assumptions don't
get edges.

### Calibration rules learned in production

These are not obvious from the definitions above; they came out of
correcting real adjudication mistakes and are worth stating explicitly so
they don't get relearned the expensive way:

- **Harmonised management-system clauses are `PARTIAL`, never
  `EQUIVALENT`.** Modern management-system standards share a common
  clause skeleton (risk-based clauses 4–10 turn up, sometimes with
  identical numbering and near-identical titles, across an information
  security management system and, say, an AI management system). The
  *mechanism* is reused, but each instance governs a different management
  system's scope — "top management commitment" for an ISMS and for an AI
  management system are not the same obligation just because the clause
  number and heading match. Treating harmonised clauses as `EQUIVALENT`
  systematically overstates coverage across every management-system pair
  in the graph, so the rule is absolute: harmonised structure is at most
  `PARTIAL`.
- **Reused boilerplate is `PARTIAL` (template reuse), not duplicate
  `EQUIVALENT`.** Internal document sets are full of shared templates —
  the same "read and acknowledge this document within N days" clause
  copy-pasted into a dozen procedures, the same self-assessment checklist
  boilerplate reused across several standards. The *wording* is identical
  or near-identical; the *object* is different in each instance (a
  different document being acknowledged, a different asset being
  assessed). Matching on wording alone would call these EQUIVALENT
  duplicates of each other, which is false — they are independent
  instances of a shared template, correctly related as `PARTIAL` (or, more
  often, simply not related to each other at all; each instance is
  related instead to whatever specific object it governs).
- **Lean into `SUPPORTS` for genuine enabling links, direction
  enabler→enabled.** Adjudicators under-use `SUPPORTS` relative to how
  often a real enabling dependency exists, because it's tempting to force
  everything into `EQUIVALENT`/`PARTIAL`/nothing. When source doesn't
  cover target's requirement but genuinely makes it possible, use
  `SUPPORTS` rather than stretching `PARTIAL` to cover an enablement
  relationship it wasn't designed for, or dropping a real dependency
  entirely.
- **Edge strength can be organisation-dependent — say so in the
  rationale, don't bend the type.** Whether a `PARTIAL` edge is "nearly
  EQUIVALENT" or "barely PARTIAL" often depends on facts about a specific
  organisation's implementation (how strictly a procedure is enforced,
  whether a control is automated or manual) that have nothing to do with
  the semantic relationship between the two requirements as written. Don't
  let that organisational context push the *relation type* up or down —
  record it as a caveat in the free-text `rationale` and let `confidence`
  carry the uncertainty. The relation type should answer "what kind of
  relationship do these two requirements have, as written", full stop.

## 4. Pipeline

Seven stages, source text to queryable graph. `verify` is its own stage,
not folded into `adjudicate` — it needs to happen after adjudication has
produced a full judgment set, and only a sample (the strong claims) goes
through it. Environment variables carry all secrets: `NEO4J_URI`,
`NEO4J_USERNAME`, `NEO4J_PASSWORD` for the graph, `CROSSWALK_OLLAMA_URL`
for the embedder (defaults to `http://localhost:11434`).

1. **Decompose** — one parser per framework, source text to node JSON:
   documents in, requirement-bearing units out (§2). The CAF
   adapter's `__main__` takes positional arguments, not flags:
   ```
   uv run python crosswalk/adapters/caf_adapter.py path/to/caf_4_0.pdf crosswalk/data/caf_4_0_nodes.json
   ```
2. **Canonicalise** — LLM writes `canonical_intent` per node, in place.
   There is no script for this step: it's a per-node (or small-batch) LLM
   call using the prompt template in `docs/AGENT_ADJUDICATION.md` §(a),
   which writes `canonical_intent` back onto the node file directly - no
   separate output file.
3. **Embed** — canonical intents to vectors, via any Ollama or
   OpenAI-compatible endpoint.
   ```
   uv run python crosswalk/embed.py --in crosswalk/data/caf_4_0_nodes.json
   ```
4. **Candidates** — cosine top-k across framework pairs, floor-filtered.
   ```
   uv run python crosswalk/candidates.py \
     --in crosswalk/data/caf_4_0_nodes.json --in crosswalk/data/iso27002_nodes.json \
     --k 5 --floor 0.55 --out crosswalk/out/candidates.json
   ```
5. **Adjudicate** — slice into batch files, hand each to an LLM with
   `crosswalk/RUBRIC.md`, merge the results back. `prep_adjudication.py`
   requires at least one `--nodes` file (it grounds each pair in verbatim
   source text) and `merge_judgments.py` requires `--prefix`:
   ```
   uv run python crosswalk/prep_adjudication.py \
     --in crosswalk/out/candidates.json --dir crosswalk/out/adjudication --batch 35 \
     --nodes crosswalk/data/caf_4_0_nodes.json --nodes crosswalk/data/iso27002_nodes.json
   # ... agent writes crosswalk/out/adjudication/out_batch_NNN.json per batch ...
   uv run python crosswalk/merge_judgments.py \
     --dir crosswalk/out/adjudication --prefix batch --out crosswalk/out/judgments.json
   ```
6. **Verify** — re-batch the strong claims (`EQUIVALENT`, high-confidence
   `PARTIAL`) for an independent adversarial pass; merge any downgrades
   back in. `prep_adjudication.py` batches *candidates*, not judgments, so
   selecting the strong claims is a one-line filter over the merged
   judgments, not a `prep_adjudication.py` flag:
   ```
   uv run python -c "
   import json
   j = json.load(open('crosswalk/out/judgments.json'))
   strong = [x for x in j if x['relation'] == 'EQUIVALENT'
             or (x['relation'] == 'PARTIAL' and x['confidence'] >= 0.8)]
   json.dump(strong, open('crosswalk/out/verification/strong_claims.json', 'w'), indent=2)
   "
   # ... refuter agent reads strong_claims.json (plus the node files for
   # titles/raw_text/canonical_intent) and writes
   # crosswalk/out/verification/downgrades.json: a JSON array of
   # {source_fw, source_ref, target_fw, target_ref, new_relation, note} ...
   uv run python crosswalk/merge_judgments.py \
     --dir crosswalk/out/adjudication --prefix batch \
     --downgrades crosswalk/out/verification/downgrades.json \
     --out crosswalk/out/judgments.json
   ```
7. **Load** — assemble typed edges from the verified judgment set, then
   write nodes + edges into Neo4j (idempotent `MERGE`; a byproduct
   `crosswalk/out/edges.json` is kept for the optional `validation/` step).
   Two commands: `build_edges.py` assembles the edges file (it reads
   `merge_judgments.py`'s output directly), then `load_neo4j.py` writes it
   into the graph.
   ```
   uv run python crosswalk/build_edges.py \
     --nodes crosswalk/data/caf_4_0_nodes.json --nodes crosswalk/data/iso27002_nodes.json \
     --judgments crosswalk/out/judgments.json \
     --out crosswalk/out/edges.json

   export NEO4J_URI=neo4j://127.0.0.1:7687
   export NEO4J_USERNAME=neo4j
   export NEO4J_PASSWORD=<from your own secret manager>
   uv run python crosswalk/load_neo4j.py \
     --nodes crosswalk/data/caf_4_0_nodes.json --nodes crosswalk/data/iso27002_nodes.json \
     --edges crosswalk/out/edges.json
   ```

Gap analysis is deliberately absent from this list — see §1. It is a
Cypher query against the loaded graph, run whenever you want the current
answer, not a pipeline stage that produces a stored artefact.

## 5. Accuracy engineering

**Recall-first candidate tuning.** Stage 4 is not the final answer, it is
a net cast wide enough that stage 5 doesn't have to compare every node
against every other node. A false positive here just costs the adjudicator
one quick "no relation" verdict; a false negative here is unrecoverable —
a pair that never becomes a candidate never gets a second look. So `k` and
`floor` are tuned generous, not precise, and tuned *empirically*: take the
cosine distribution of pairs you already know are true (an existing
judgment set, or a small hand-labelled seed set), and use its **p5** as
the floor guide — a floor near the 5th percentile of true-pair cosines
means roughly 95% of known true pairs clear it, which is the point; don't
guess a threshold and hope.

**Judges see verbatim source text, not only canonical intents.**
`canonical_intent` is a comparison aid; it is also a normalisation, and
normalisation is lossy by design. Every batch handed to an adjudicator
carries both sides' `canonical_intent` *and* a slice of both sides'
`raw_text`, so a judgement can be grounded in the actual wording when the
normalisation has flattened a distinction that matters (a caveat, an
exception, a scope qualifier).

**Batch outputs are written to files by the adjudicating agent, one file
per batch.** This is crash-resilient by construction: a batch of 35 pairs
that dies halfway through costs you that one batch, not the run. It also
makes the workload trivially parallelisable across multiple agent
sessions or a cheaper model for volume, because each batch is
self-contained (it carries its own refs, titles, intents, verbatim text,
and cosine score — nothing else needs to be in context) and merging is
just concatenating and deduplicating JSON files. See
`docs/AGENT_ADJUDICATION.md`.

**Adversarial verification of strong claims.** `EQUIVALENT` verdicts and
high-confidence `PARTIAL` verdicts are the ones a downstream gap query
will treat as "covered, stop looking" — so they're the ones that most
need to be right. Rather than trust the first adjudication pass, route
these through a second, independent pass whose only job is to *argue
against* the claimed relation (a refuter prompt, not a re-confirmation
prompt — asking the same model to check its own work again mostly just
reproduces the same answer). Downgrades from the refuter pass (typically
`EQUIVALENT` → `PARTIAL`, or `PARTIAL` → dropped) get merged back into the
judgment set with the refuter's specific objection appended to the
`rationale`, not silently overwritten.

*Does it actually catch anything?* In the reference deployment the refuter
pass changed **293 verdicts**, of which **61 removed the relationship
altogether** — a verification stage that never overturns anything is
decoration, so this number is worth measuring rather than assuming. Two
failure modes dominated, and both are systematic rather than random:

- **A narrower internal implementation claiming `EQUIVALENT` with a broader
  external control.** A cloud-only clock-synchronisation rule was judged
  equivalent to a control requiring synchronisation across the whole
  estate; correct answer `PARTIAL`. This pattern — real coverage of a real
  subset, mistaken for full coverage — is the single most common downgrade,
  and it is precisely the error a gap query cannot survive, because
  `EQUIVALENT` tells the query to stop looking.
- **Shared boilerplate judged `EQUIVALENT` to itself across documents.**
  The template-reuse rule in §3 was learned here: 147 of the intra-corpus
  downgrades were near-identical clauses whose *wording* matched but whose
  *object* differed in each instance.

Both are cases where the first pass was reasonable and wrong in the
expensive direction. Neither would have been caught by re-asking the same
model to check its own work.

**Conflict flagging for intra-corpus contradictions.** When adjudicating
pairs within your own document set (§8) rather than against an external
framework, you will find pairs where two of your own documents both claim
jurisdiction over the same requirement and *disagree* — different
retention periods, different named approval authorities, different
timeframes for the same control. Don't silently pick a winner. Flag the
edge (a boolean `conflict` property alongside the relation type) and
surface it for governance review; a crosswalk that quietly resolves
internal contradictions in the adjudicator's favour hides exactly the
finding you built the graph to find.

## 6. The embedder validation gate

Swapping the embedding model — moving to a bigger local model, a hosted
one, or just a different checkpoint — is not a drop-in change, because
stage 4's candidate generation depends on absolute cosine geometry, and
that geometry is specific to the embedding space it was tuned in. The
safety gate before trusting a new embedder for new adjudication work is
`crosswalk/validate_embedder.py`: re-embed every node in the new
space, then check, for every edge you've *already* adjudicated (excluding
any that were deterministic identity matches rather than embedding-
derived), whether the new space's stage-4 regime — same top-k, same pool
rules — would have surfaced that pair as a candidate at all.

A few things this validation makes visible that intuition won't:

- **Recall is reported per relation type as well as overall**, because
  different relation types don't sit at the same cosine distance from
  each other. `INFORMS` pairs in particular tend to be semantically loose
  and are usually the first casualty when a floor tightens — an overall
  recall number can look fine while quietly dropping most of your
  `INFORMS` edges.
- **Floors must be re-derived per space, not carried over.** The true-pair
  cosine distribution's shape — especially its lower tail, the p5 that
  drives the floor — varies a lot between embedding models. A floor tuned
  for one model applied to another's vectors is not conservative, it's
  arbitrary; recompute p1/p5/p10/p25/p50 for the new space and set the new
  floor from that, not from memory of the old one.
- **Evaluate each model with its own native prompt convention.** Some
  embedding models expect a task instruction or a `query:`/`passage:`
  prefix wrapped around the text; feeding every candidate model the same
  bare canonical intent without its expected convention systematically
  handicaps whichever model wants the wrapper, and the recall comparison
  between models becomes a comparison of who got their calling convention
  right rather than whose embedding space is better.

Only once a candidate model clears recall on the existing judgment set,
with its own re-derived floor, should it be trusted to generate candidates
for *new* pairs you haven't adjudicated yet.

## 7. External validation against a metaframework hub

Framework mapping hubs — most usefully the **Secure Controls Framework
(SCF)**, whose STRM (Set Theory Relationship Mapping) methodology
cross-maps a very large number of frameworks against one canonical control
set — give you an independent referee for free, provided you treat its
signal correctly. `validation/` in this kit is the optional workflow for
this comparison; it is not shipped with SCF's own data (see `NOTICE.md`)
— download the SCF mapping spreadsheet yourself and point the validation
scripts at it.

**The principle.** If SCF maps a single control to a ref in your framework
A *and* a ref in your framework B, SCF is independently asserting that
those two refs are related — it didn't come from your embeddings or your
adjudicator. Compare that SCF-attested co-mapping against your own derived
edges, for every framework pair you both cover, and bucket every
comparable pair into exactly three outcomes:

- **agreement** — you have an edge, and SCF co-maps the pair. Externally
  corroborated.
- **ours_only** — you have an edge, SCF does not co-map it. Could be a
  finer semantic link than SCF's control granularity captures; could be
  overreach. Worth a look, not automatically wrong.
- **scf_only** — SCF co-maps the pair, you have no edge. Could be a
  genuine candidate-generation miss (raise it back through stage 4/5);
  could be SCF's control simply bundling two loosely related refs
  together. Also worth a look, not automatically a miss.

**Expect hub-bundling, and use your own data to confirm it's real, not
noise.** SCF controls are often broad, so two refs that both satisfy one
broad SCF control need not be substantively related *to each other* at
all. The tell is quantitative: compute the correlation between SCF's
attested relationship strength (STRM's relationship type × strength-of-
relationship score) and your own pairwise cosine, across every SCF
co-mapped pair you can also embed. In a healthy comparison this
correlation sits near zero — SCF's bundling strength and your semantic
similarity are measuring different things — which tells you `scf_only`
needs human review by content, not blanket import, and that a low
correlation is expected behaviour, not a bug in either dataset.

**Use your own confirmed-edge cosine distribution as the review bar, not
an arbitrary number.** For triaging the `scf_only` list, compute the
cosine distribution of the edges you've *already* adjudicated and trust,
and set HIGH/MEDIUM/LOW disposition thresholds from its percentiles (e.g.
HIGH at or above your confirmed-edge median, MEDIUM down to its p25). Two
independent signals agreeing — SCF strong *and* cosine at or above your
own confirmed-edge bar — is a genuinely confident candidate for a missed
edge; SCF strong but cosine low is much more likely SCF bundling.

**Watch granularity mismatches before concluding anything.** If your
nodes are finer-grained than SCF's typical mapping level (individual
outcomes vs. whole principles is the common case), roll one side up to
match before comparing — otherwise granularity mismatch alone will
manufacture apparent disagreement that has nothing to do with the
substance of either mapping.

## 8. Extending to an internal corpus

Everything above assumes both sides of a pair are already clean,
framework-native nodes. Real internal documents need one more step first:
decomposition into typed units, because a document is not a node, it is a
container the adapter has to open up.

**Documents lie about their own type.** A document titled "Policy" often
contains procedural how-to steps; a document titled "Procedure" often
contains policy-level statements of intent. Classify each decomposed unit
on its own content — what it actually asserts — not on what the parent
document's title claims it is. It's worth recording the document's
claimed type *and* the more granular per-unit classification side by
side; the gap between the two is itself a useful signal (a document that
claims to be a single type but decomposes into a wide mix of unit kinds is
usually a sign the document needs restructuring, independent of anything
the crosswalk finds).

**Same-document blocking.** When generating internal-corpus candidates
for cross-document duplicate or near-duplicate detection, exclude
same-document pairs from that candidate pool. A document's own units are
trivially related to each other through the containment hierarchy, not
through a semantic candidate edge, and letting same-document pairs into
the pool just wastes adjudication budget confirming things the hierarchy
already states.

**Unit-identity preservation across document versions.** When a document
is revised, don't decompose the new version from a blank slate — give the
decomposition step the previous version's unit list as context, so a
requirement that hasn't materially changed keeps the same unit identity
across versions. This matters because edges are asserted about *units*,
not documents: an `EQUIVALENT` edge earned by a unit in v1 should still
apply to the logically same unit in v2 without re-adjudication, and a
version-over-version diff of what actually changed only means something
if unit identity is stable across the diff.

**Decommission impact queries.** Because typed edges point at specific
units, not whole documents, retiring or superseding a document turns
"what does withdrawing this break" into a graph query — everything with an
edge pointing at one of this document's units — instead of a manual
cross-reference exercise done by someone who has to remember to do it.

**A note on bi-temporal layering.** Don't parse "effective date" or
"review date" out of a document's body and treat it as ground truth. Those
dates go stale the moment a document is revised without a full
republication, and a document that hasn't been touched in three years may
say "review date: last year" indefinitely. Validity dates are a property
of *governance*, not of *content* — they belong in a separate temporal
layer sourced from whatever system of record actually governs document
lifecycle in your organisation (a document/records management system),
never derived from parsing the document text itself. Keep that layer
distinct from the parsed node data so the two can be updated
independently.

## 9. Cost and scale

This methodology was built out against a reference deployment mapping five
public frameworks (NCSC CAF 4.0, ISO 27001, ISO 27002, ISO 42001, NHS DSPT)
and an internal policy estate of roughly 113 documents into one graph. The
measured funnel:

| Stage | Count |
|---|---|
| Nodes decomposed from source | 2,322 |
| Mappable leaf nodes (canonicalised + embedded) | 1,595 |
| Candidate pairs proposed (stage 4) | 14,436 |
| Verdicts returned (stage 5) | 14,346 |
| — `no_relation`, dropped | 6,112 (42.6%) |
| — typed edges kept | 8,234 |
| Verdicts changed by refuter pass (stage 6) | 293 (61 edges removed) |
| Typed edges loaded | 8,455 |
| `CONTAINS` hierarchy edges | 2,084 |
| **Total relationships in graph** | **10,539** |

Final relation mix: `PARTIAL` 5,070 · `SUPPORTS` 2,898 · `EQUIVALENT` 278 ·
`INFORMS` 209. The heavy skew toward `PARTIAL` over `EQUIVALENT` (18:1) is
the calibration rules in §3 working — harmonised clauses and reused
boilerplate are `PARTIAL` by rule, and an adjudicator left to its own
instincts produces far more `EQUIVALENT` than survives scrutiny.

That 42.6% rejection rate is not a defect in candidate generation; it is
the recall-first tuning in §5 behaving as designed. A candidate stage that
proposed only pairs that survived adjudication would be a candidate stage
tuned too tight, and the pairs it silently failed to propose would never be
recoverable.

Two things kept that affordable:

- **Adjudication ran through a subscription coding agent, not metered API
  calls.** Batches of ~35 pairs, one agent turn per batch, output written
  to a file per batch (§5) — the marginal cost of adjudicating one more
  batch is a few minutes of wall-clock time and agent-session budget, not
  a per-token bill. `docs/AGENT_ADJUDICATION.md` covers this workflow,
  including running parallel agent fleets against different batch ranges.
- **Embedding is wholly local.** Every embedding call in this pipeline
  goes to a local endpoint (`CROSSWALK_OLLAMA_URL`, defaulting to
  `http://localhost:11434`) — re-embedding the entire corpus for the
  validation gate in §6, or re-running candidate generation after a
  parsing fix, costs compute time and nothing else, which is what makes
  it reasonable to re-run those steps as often as the pipeline needs
  rather than treating them as expensive one-off operations to be avoided.

Your own graph will be smaller or larger depending on how many frameworks
and documents you feed it, but the pipeline — and its cost profile — is
the same one.
