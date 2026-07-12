# Adjudication rubric

Hand this file to an LLM together with one batch file from
`crosswalk/out/adjudication/` (see `docs/AGENT_ADJUDICATION.md` for the
full workflow). It is the operational form of `METHODOLOGY.md` §3 —
read that first if you want the reasoning behind these rules, this file is
the checklist for applying them.

Each batch entry gives you both sides' `canonical_intent` **and** a slice
of verbatim `raw_text`, plus the cosine score that proposed the pair as a
candidate. Judge the actual wording, not just the canonicalised intent —
canonicalisation is a normalisation for comparison, and normalisation
occasionally smooths away a distinction (a caveat, an exception, a scope
qualifier) that matters to the verdict. The cosine score is context for
how the pair was found; it is not evidence for or against a relationship
and should not influence your confidence.

## Relations

Exactly one relation per pair, or none. If none of the four tests below
is satisfied, write the pair anyway with `relation: no_relation` — do not
omit the pair from your output. This keeps 1:1 pair accounting for every
batch (every candidate pair in, one verdict line out), which is what makes
the batch auditable; `no_relation` lines are dropped downstream and never
become edges or stored facts.

| Relation | Meaning | Decision test |
|---|---|---|
| `EQUIVALENT` | Source and target require substantively the same thing, at the same scope. | Would satisfying one, in full, satisfy the other, in full? |
| `PARTIAL` | Source addresses only part of target's scope, or vice versa. | Does satisfying source leave a meaningful piece of target's requirement unaddressed (or vice versa)? |
| `SUPPORTS` | Source is a precondition that makes target achievable, without itself being the thing that satisfies target. | Does target's requirement become impossible, or much weaker, without source in place — even though source doesn't itself do what target asks? |
| `INFORMS` | Source supplies context, definitions, or scope-narrowing relevant to interpreting target, with no obligation overlap. | Would you cite source in a footnote explaining target, without claiming source *does* any part of what target requires? |

## Direction

Every edge is `source → target`, meaning **source addresses or enables
target**. Assign direction from the actual relationship, never
arbitrarily:

- `EQUIVALENT`: still pick a direction for the edge itself — either order
  is defensible since the claim holds both ways. Symmetry is derived
  downstream (`build_edges.py` sets `symmetric: true` for every
  `EQUIVALENT` edge from the relation type alone), not something you
  record here.
- `PARTIAL`, `SUPPORTS`, `INFORMS`: direction is load-bearing. Reversing
  it changes the claim's meaning (`A PARTIAL B` = "A covers part of B's
  scope" is not the same claim as `B PARTIAL A`). For `SUPPORTS`
  specifically, direction is always enabler → enabled.

## Confidence (0–1)

Confidence expresses how certain *you* are in the relation type you
chose, given the text in front of you — not how important the pair is,
and not how strictly an organisation happens to enforce either side.

- **0.90–1.00** — near-identical wording and scope; a reasonable second
  reviewer would not disagree.
- **0.70–0.89** — clearly the relation type you chose, but with some
  hedge (a scope qualifier, an inferred rather than stated purpose, a
  translation between outcome-phrasing and prescriptive-phrasing that you
  are confident in but that required interpretation).
- **0.50–0.69** — plausible, but meaningfully uncertain; you can defend
  the verdict but wouldn't be surprised if a refuter pass downgraded it.
- **Below 0.50** — you should probably not be writing this edge at all.
  If you're this unsure, prefer omitting the pair over recording a
  low-confidence guess.

## Calibration rules

These override "looks right" instincts learned from a small number of
examples — apply them even when a pair doesn't feel like it needs the
reminder:

1. **Harmonised management-system clauses are `PARTIAL`, never
   `EQUIVALENT`.** If both sides are structural clauses from different
   management-system standards that share numbering or near-identical
   titles (e.g. both are "leadership and commitment" clauses from two
   different management systems), the *mechanism* is shared but the
   *scope* is not — each governs a different management system. Cap this
   at `PARTIAL`, always, regardless of how close the wording looks.
2. **Reused boilerplate is `PARTIAL` (template reuse), not duplicate
   `EQUIVALENT`.** If both sides use near-identical template wording
   (an acknowledgement clause, a self-assessment checklist preamble) but
   govern different objects (different documents, different assets,
   different systems), that is template reuse, not a duplicate
   requirement — `PARTIAL` at most, and often no relation between the two
   *instances* at all (each instance may instead relate to whatever
   specific object it governs).
3. **Lean into `SUPPORTS` for genuine enabling links.** Don't stretch
   `PARTIAL` to cover an enablement relationship, and don't drop a real
   dependency because neither side "covers" the other. If source makes
   target achievable without itself satisfying target, that's `SUPPORTS`,
   direction enabler → enabled.
4. **Organisation-dependent strength goes in the rationale, not the
   type.** If your sense of how strong a `PARTIAL` edge is depends on
   facts about a specific implementation (how strictly something is
   enforced, whether a control is automated or manual) rather than on the
   text itself, say so in `rationale` and reflect it in `confidence` — do
   not let it push you toward `EQUIVALENT` or away from writing the edge
   at all. The relation type answers "what kind of relationship do these
   two requirements have, as written" and nothing else.

## Output

Write one JSON object per pair in the batch — every pair, not just the
ones you found a relation for — one per line — JSONL:

```json
{"source_fw": "...", "source_ref": "...", "target_fw": "...", "target_ref": "...", "relation": "EQUIVALENT|PARTIAL|SUPPORTS|INFORMS|no_relation", "confidence": 0.0, "rationale": "..."}
```

- `source_fw` / `target_fw` — framework codes as given in the batch file.
- `source_ref` / `target_ref` — native refs as given in the batch file
  (not internal node ids).
- `relation` — exactly one of the four typed values above, or
  `no_relation` if none of the four tests is satisfied.
- `confidence` — a number in `[0, 1]`, per the guidance above.
- `rationale` — one sentence. State what in the text justifies the
  relation and direction you chose. If a calibration rule applied, name
  it (e.g. "harmonised MS clause, capped at PARTIAL").

`no_relation` lines are written for audit (1:1 pair accounting for the
batch) but are dropped downstream by `merge_judgments.py` — they never
become edges or stored facts; gaps are computed at query time.

## Worked examples

Framework codes below (`SEC-STD`, `GOV-FW`, `AI-MS`, `INTERNAL`) are
illustrative placeholders, and the clause text is written for this rubric,
not quoted from any real standard — substitute your own frameworks' actual
wording when you run this for real.

**1. EQUIVALENT**

> `SEC-STD` control `A.9.2.3` — *"User access rights shall be reviewed at
> planned intervals and after any change in role, and adjusted or revoked
> as necessary."*
> `GOV-FW` outcome `B2.b` — *"You review user access rights on a regular
> basis, and promptly remove or adjust access following a change in
> role."*

```json
{"source_fw": "SEC-STD", "source_ref": "A.9.2.3", "target_fw": "GOV-FW", "target_ref": "B2.b", "relation": "EQUIVALENT", "confidence": 0.88, "rationale": "Same obligation (periodic access review, adjustment on role change) stated prescriptively vs outcome-wise; no scope difference."}
```

**2. PARTIAL — harmonised management-system clause (calibration rule 1)**

> `SEC-STD` clause `6.1` (information security management system) —
> *"The organisation shall determine risks and opportunities that need to
> be addressed to give assurance the management system can achieve its
> intended outcomes."*
> `AI-MS` clause `6.1` (AI management system) — *"The organisation shall
> determine risks and opportunities that need to be addressed to give
> assurance the AI management system can achieve its intended outcomes."*

```json
{"source_fw": "SEC-STD", "source_ref": "6.1", "target_fw": "AI-MS", "target_ref": "6.1", "relation": "PARTIAL", "confidence": 0.75, "rationale": "Harmonised MS clause, capped at PARTIAL: identical risk-planning mechanism, but each governs a different management system's scope (information security vs AI)."}
```

**3. PARTIAL — reused boilerplate (calibration rule 2)**

> `INTERNAL` document `SOP-014` §1 — *"All staff must read and acknowledge
> this SOP within 10 working days of publication."*
> `INTERNAL` document `SOP-027` §1 — *"All staff must read and acknowledge
> this SOP within 10 working days of publication."*

```json
{"source_fw": "INTERNAL", "source_ref": "SOP-014#1", "target_fw": "INTERNAL", "target_ref": "SOP-027#1", "relation": "PARTIAL", "confidence": 0.55, "rationale": "Template-reuse boilerplate, not a duplicate requirement: identical acknowledgement clause but each governs acknowledgement of a different SOP."}
```

**4. SUPPORTS**

> `SEC-STD` control `A.8.5` — *"Unique user identifiers and centralised
> authentication shall be maintained for all systems processing sensitive
> data."*
> `GOV-FW` outcome `C1.a` — *"Security-relevant events are logged and can
> be attributed to an individual user or system component."*

```json
{"source_fw": "SEC-STD", "source_ref": "A.8.5", "target_fw": "GOV-FW", "target_ref": "C1.a", "relation": "SUPPORTS", "confidence": 0.80, "rationale": "Enabling link, direction enabler->enabled: without unique attributable identifiers, events cannot be attributed to an individual, but A.8.5 does not itself require logging or attribution."}
```

**5. INFORMS**

> `INTERNAL` document `POL-007` §2 (definitions) — *"For the purposes of
> this policy, 'sensitive data' means any data classified Confidential or
> above under the organisation's data classification standard."*
> `SEC-STD` control `A.5.12` — *"Information shall be classified according
> to the organisation's information classification scheme, and sensitive
> data handled accordingly."*

```json
{"source_fw": "INTERNAL", "source_ref": "POL-007#2", "target_fw": "SEC-STD", "target_ref": "A.5.12", "relation": "INFORMS", "confidence": 0.65, "rationale": "Definitional context only: POL-007#2 defines the term A.5.12 relies on, but imposes no classification obligation of its own."}
```
