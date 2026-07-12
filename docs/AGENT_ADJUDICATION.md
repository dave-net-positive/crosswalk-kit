# Running adjudication with an AI subscription agent

`METHODOLOGY.md` §9 makes the case for why this kit's canonicalise and
adjudicate steps are designed to run through a subscription coding agent
(Claude Code or similar) rather than metered API calls: the marginal cost
of one more batch is wall-clock time inside a session you're already
paying for, not a per-token bill. This document is the practical
workflow — how to structure the work so an agent (or a fleet of them) can
run it reliably, plus copy-paste prompt templates for the three LLM tasks
in the pipeline: canonicalisation, adjudication, and adversarial
verification.

## The pattern: one batch, one file, every time

Give the agent exactly two things per turn:

1. `crosswalk/RUBRIC.md` (or, for canonicalisation, the short prompt in
   §1 below) — the rules, once, or resupplied at the top of each turn if
   the agent's context doesn't persist between turns.
2. One batch file from `crosswalk/out/adjudication/` (produced by
   `crosswalk/prep_adjudication.py`) — nothing else. Each batch
   is self-contained: refs, titles, canonical intents, verbatim source
   text, and the cosine score that proposed the pair, for every pair in
   that batch. The agent needs nothing else in context to judge it.

The agent's only output is a file: `out_<prefix>_NNN.json`, matching the
input batch's number, written next to the input batch (or wherever you
tell it to). This is the crash-resilient contract that makes the rest of
this workflow possible:

- A batch that fails, times out, or gets a malformed response costs you
  that one batch — re-run it. Nothing upstream or downstream is affected.
- `crosswalk/merge_judgments.py` just globs `out_*.json`,
  concatenates, and keys on `(source_fw, source_ref, target_fw,
  target_ref)` to spot collisions and apply any verifier downgrades.
  It doesn't care how many turns, sessions, or models produced the files,
  or in what order.
- You can inspect progress at any time by counting how many `out_*.json`
  files exist next to how many batch files were written — there is no
  hidden state.

## Parallel fleets

Because batches are independent and self-contained, adjudication
parallelises trivially:

- **Volume**: run several agent sessions concurrently, each claiming a
  different range of batch numbers, to get through a large candidate set
  faster. A cheaper/faster model is a reasonable choice for this pass —
  it's high-volume, individually low-stakes (any given verdict is one
  edge among thousands, and wrong verdicts are exactly what stage 6 below
  exists to catch), and errors are cheap to find and re-run.
- **Verification**: run a *separate* pass, after the adjudication fleet
  finishes, over the strong claims specifically (`EQUIVALENT` and
  high-confidence `PARTIAL`). `prep_adjudication.py` batches *candidates*,
  not judgments, so it has no `--relation`/`--min-confidence` flags;
  select the strong claims with a one-line filter over the merged
  judgments instead (see `METHODOLOGY.md` §4 stage 6 for the exact
  command). Use your most capable model here, and a
  **refuter** prompt (§3 below), not a re-confirmation prompt — asking
  the same kind of pass to check its own work tends to just reproduce the
  original answer.

Nothing about running these as parallel agent sessions requires special
tooling beyond what you already have: open a batch file, apply the
prompt, save the output file, move to the next batch. A fleet is just
several of those running at once against different batch ranges.

## Prompt templates

### (a) Canonicalisation

One call per node, or a small batch of nodes at once if your agent
handles structured batches well. Input: a node's `raw_text` (and `title`,
for context). Output: `canonical_intent` in the fixed two-part shape from
`METHODOLOGY.md` §2.

```
You are canonicalising compliance requirements into a fixed two-part
shape, for embedding and cross-framework comparison later.

For the node below, write ONE canonical_intent string in exactly this
shape:

    requires: <the actual obligation, stated plainly, stripped of
    framework-specific idiom>; in order to: <the purpose it serves>.

Rules:
- State the obligation plainly regardless of whether the source is
  phrased prescriptively ("shall implement...") or as an outcome ("you
  have achieved..."). Both should canonicalise to the same register.
- If the source doesn't state a purpose explicitly, infer the most
  plausible purpose from context rather than leaving it out — every
  canonical_intent has both clauses.
- Do not add requirements that aren't in the source text. Do not drop
  a qualifier, exception, or scope limit that changes what's actually
  required — canonicalise the idiom, not the substance.
- One sentence. No preamble, no explanation, just the canonical_intent
  string.

Node:
  framework: {framework}
  native_ref: {native_ref}
  title: {title}
  raw_text: {raw_text}

Output the canonical_intent string only.
```

### (b) Adjudicating a batch

```
You are adjudicating candidate requirement pairs for a cross-framework
compliance crosswalk. Apply crosswalk/RUBRIC.md exactly — relation
definitions, direction rule, confidence guidance, and the calibration
rules are non-negotiable, not stylistic suggestions.

The rubric is attached: {contents of crosswalk/RUBRIC.md, or a path to it
if your agent can read files directly}.

Here is one batch of candidate pairs, each with both sides' framework,
ref, title, canonical_intent, a slice of verbatim raw_text, and the
cosine score that proposed the pair (context only — not evidence for or
against a relationship):

{contents of crosswalk/out/adjudication/batch_NNN.json}

For each pair:
- Judge against the verbatim raw_text, not just the canonical_intent.
- Apply exactly one relation (EQUIVALENT / PARTIAL / SUPPORTS / INFORMS),
  or "no_relation" if none is defensible. Write every pair - do not omit
  any of them, typed or not.
- Apply the calibration rules where they're relevant, and name the rule
  in the rationale when you do.

Write your output to: crosswalk/out/adjudication/out_batch_NNN.json
(match the input batch's number). One JSON object per line (JSONL),
each exactly matching this schema:

{"source_fw": "...", "source_ref": "...", "target_fw": "...",
 "target_ref": "...", "relation": "...", "confidence": 0.0,
 "rationale": "..."}

One line per pair in the batch, no exceptions - "no_relation" lines are
dropped downstream by merge_judgments.py, not by you. Write the file;
don't just print the judgments back to me.
```

### (c) Adversarial verification of a strong claim

Use this for the stage-6 verification pass, on `EQUIVALENT` and
high-confidence `PARTIAL` judgments only — not the full judgment set.

```
You are a refuter. Your job is to argue AGAINST an existing adjudication,
not to confirm it. A relation was previously judged as {relation}
(confidence {confidence}) between these two requirements:

  {source_fw} {source_ref} — {source_title}
  canonical_intent: {source_canonical_intent}
  raw_text: {source_raw_text}

  {target_fw} {target_ref} — {target_title}
  canonical_intent: {target_canonical_intent}
  raw_text: {target_raw_text}

  Original rationale: {rationale}

Find the strongest available objection to this judgment. Specifically
check:
- Is this actually a harmonised management-system clause or reused
  boilerplate being over-called as EQUIVALENT rather than PARTIAL
  (crosswalk/RUBRIC.md calibration rules 1 and 2)?
- Does the target's scope include something the source doesn't actually
  address, or vice versa — i.e. is this really PARTIAL, or even SUPPORTS,
  dressed up as EQUIVALENT?
- Is there a qualifier, exception, or scope limit in either raw_text that
  the canonical_intent smoothed away and the original judgment missed?

Output ONE JSON object, in exactly this shape (it is consumed directly by
`merge_judgments.py --downgrades`):

{"source_fw": "{source_fw}", "source_ref": "{source_ref}",
 "target_fw": "{target_fw}", "target_ref": "{target_ref}",
 "verdict": "confirmed|downgrade|reject",
 "new_relation": "EQUIVALENT|PARTIAL|SUPPORTS|INFORMS|no_relation|null",
 "note": "one sentence stating your strongest objection, or 'none found'
   if the original judgment holds"}

"downgrade" means you found a real problem with the relation TYPE (state
the corrected type in new_relation). "reject" means there should be no
edge at all — set new_relation to "no_relation". "confirmed" means the
original judgment holds under scrutiny — set new_relation to null. Do not
default to "confirmed" without stating what objection you actually
checked and ruled out.
```

Collect every object whose verdict is not "confirmed" into a JSON array
(e.g. `downgrades.json`) and re-run
`crosswalk/merge_judgments.py --downgrades downgrades.json ...`. The
merge applies each downgrade by pair key, appends the refuter's `note`
to the edge's `rationale` (so the final graph carries both the original
judgement and what survived scrutiny), caps confidence at 0.75, and a
"reject" (`new_relation: "no_relation"`) is dropped with the other
no_relation lines — extra keys like `verdict` are ignored.
