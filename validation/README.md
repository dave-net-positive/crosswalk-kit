# External validation with SCF STRM

This is an **optional** module. It does not feed the main pipeline (parse →
canonicalise → embed → candidates → adjudicate → load). It gives you a second,
independent opinion on the edges your pipeline produced, sourced from a
third-party control mapping instead of your own LLM.

## What STRM is

The [Secure Controls Framework (SCF)](https://securecontrolsframework.com)
publishes a "Set Theory Relationship Mapping" (STRM) for many frameworks and
laws. STRM follows the method in
[NIST IR 8477](https://csrc.nist.gov/pubs/ir/8477/final) ("Evaluating and
Improving NIST Cybersecurity Resources"): rather than a flat "control X maps
to control Y" table, each row asserts a typed **set relationship** between one
element of a focal document (e.g. an ISO 27001 clause, a CAF outcome, a GDPR
article) and one SCF control — `Equal`, `Subset Of`, `Superset Of`,
`Intersects With`, or `No Relationship` — plus a `Strength of Relationship`
(SoR) rating.

Because STRM routes every framework through the same SCF control catalogue,
two framework elements that both map to the same SCF control are an
**independent, third-party assertion that those two elements are related** —
independent of whatever LLM produced your own crosswalk edges.

## Getting the workbooks

STRM workbooks are published per framework on the SCF site
(securecontrolsframework.com → "Resources" → STRM downloads, or via the paid
Excel/CSV bundle — the bundle is a paid product; individual STRM PDFs are
free). This module expects the `.xlsx` workbook format. If you only have the
PDF, you will need to export or re-key the relevant columns yourself.

**Licence caveat: SCF content is CC BY-ND.** ND ("No Derivatives") means you
may use SCF's mapping data, but you may not redistribute a modified/derived
version of it. In practice:
- Do not commit STRM workbooks, or anything derived from them, into this
  repository or any public fork.
- `validation/out/` (where both scripts write their output) is already
  covered by the top-level `.gitignore` — keep it that way.
- Treat `validation/out/scf_strm.json` and the validation reports as local,
  disposable working files, not shippable artefacts.

## The two scripts

### 1. `scf_strm_adapter.py` — parse STRM workbooks into JSON

STRM workbooks have their header on row 5. This script reads the fixed column
layout (`FDE #`, `FDE Name`, `STRM Relationship`, `SCF Control`, `SCF #`,
`Strength of Relationship`), drops `No Relationship` rows, and writes:

```
{framework: {scf_id: [{ref, name, rel, sor}, ...]}}
```

Frameworks and their workbook filenames are supplied on the command line —
nothing is hardcoded:

```bash
python validation/scf_strm_adapter.py \
    --dir path/to/strm/workbooks \
    --map ISO27001=scf-strm-general-iso-27001-2022.xlsx \
    --map ISO27002=scf-strm-general-iso-27002-2022.xlsx \
    --map ISO42001=scf-strm-general-iso-42001-2023.xlsx \
    --map CAF=scf-strm-emea-gbr-caf-4-0.xlsx \
    --ref-style CAF=caf \
    --out validation/out/scf_strm.json
```

`--map` is repeatable (`FRAMEWORK=filename.xlsx`). `--ref-style` is repeatable
too (`FRAMEWORK=STYLE`) and controls how the raw `FDE #` cell text is
normalised into a bare ref, since each STRM workbook's focal document numbers
its elements differently:

| style     | example input                  | normalised ref |
|-----------|---------------------------------|----------------|
| `iso`     | `A.5.17 Authentication info`    | `A.5.17`       |
| `caf`     | `A1.a Board direction`          | `A1.a`         |
| `article` | `Article 5(1)(a)`               | `Article 5`    |
| `section` | `Section 2.3 - Access control`  | `Section 2.3`  |

`iso` is the default for any framework not given an explicit `--ref-style`.
Pick the style that matches how the target framework's own refs look — the
important thing is that the ref format here matches `native_ref` in the node
files you generated for that framework earlier in the pipeline, since that is
what `scf_validate.py` joins on.

### 2. `scf_validate.py` — compare SCF co-mappings against your edges

"Co-mapping" reasoning: if SCF control `X` maps to both `ISO27001 5.17` and
`CAF A1.a`, that is SCF asserting those two refs are related. This script
inverts the STRM output by SCF control, builds every such co-mapped pair for
every pair of frameworks present in the STRM output (frameworks and pairs are
**inferred from the STRM file's top-level keys** — nothing is hardcoded), and
compares that set against your own derived edges:

```bash
python validation/scf_validate.py \
    --strm validation/out/scf_strm.json \
    --nodes out/caf_nodes.json \
    --nodes out/iso27001_nodes.json \
    --nodes out/iso27002_nodes.json \
    --nodes out/iso42001_nodes.json \
    --edges out/edges.json \
    --rollup CAF
```

`--nodes` is repeatable — pass every node file whose frameworks you want
covered. `--edges` is your pipeline's derived-edges JSON
(`{"edges": [{"source", "target", "relation"}, ...]}`). `--rollup FRAMEWORK`
is repeatable and opt-in: it truncates that framework's refs at the final
`.segment` before comparing (e.g. `A1.a` → `A1`) for frameworks whose node
granularity is finer than the granularity SCF itself maps at (our CAF case:
our nodes are outcome-level `A1.a`, SCF often maps at principle level `A1`,
so rolling up avoids understating agreement purely on a granularity
mismatch). Leave it off for frameworks where your node granularity already
matches SCF's.

Output: `validation/out/scf_validation_report.md` (human-readable, with a
summary table, per-pair breakdowns, and example pairs) and the same data as
`scf_validation_report.json`.

## Reading the three result classes

- **agreement** — you have an edge between two refs, and SCF independently
  co-maps them via a shared control. This is your strongest signal: an edge
  corroborated by a source your own pipeline never saw.
- **ours_only** — you have an edge, but SCF does not co-map that pair (even
  though both refs are SCF-covered). Not necessarily wrong: your embedding +
  adjudication step can find finer, more specific relationships than SCF's
  control-level granularity ever routes through. Worth a light review pass,
  not a panic.
- **scf_only** — SCF co-maps two refs that are both nodes in your graph, but
  you have no edge between them. Treat these as **candidates for review**,
  not confirmed misses — see the hub-bundling warning below before
  adjudicating any of them.

### Hub-bundling warning

SCF controls vary enormously in breadth. A broad "umbrella" SCF control can
map to dozens of refs across a framework, which means it will co-map *every*
pairing among those refs — most of which are only loosely related in
practice, not because of a real substantive link but because they both
happen to touch the same broad control. This inflates `scf_only` with
low-value candidates. Don't treat a large `scf_only` count as "we missed N
edges" — inspect a sample first.

### Recommendation: gate `scf_only` on your own cosine distribution

Before spending adjudication budget on `scf_only` candidates, pull the cosine
similarity distribution of the edges you *did* confirm during your own
adjudication step. Compute the cosine similarity for each `scf_only` pair
using the same embeddings you already generated, and only send pairs whose
cosine sits inside (or close to) your confirmed-edge distribution back
through adjudication. Pairs far below that distribution are much more likely
to be hub-bundling noise than genuine missed edges, and are usually not worth
the LLM calls.
