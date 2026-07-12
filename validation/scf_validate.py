"""
scf_validate.py - Use SCF STRM mappings as an external referee for our derived edges.

Principle: when a single SCF control maps to a ref in framework A AND a ref in
framework B (per scf_strm_adapter.py's output), SCF is independently asserting
that those two refs are related (both satisfy that control). That is an
SCF-attested co-mapping. We compare the set of SCF-attested co-mappings against
our embedding+adjudication-derived edges, for every pair of frameworks present
in the STRM output - no framework pairing is hardcoded here.

For each pair, within the universe where a comparison is even possible (both
refs exist as nodes in our graph AND both are covered by SCF), we report:
  - agreement : we have an edge AND SCF co-maps it   (externally corroborated)
  - ours_only : we have an edge, SCF does not co-map  (review: finer link, or over-reach)
  - scf_only  : SCF co-maps, we have no edge          (review: candidate miss, or SCF broad-control bundling)

These are REVIEW SIGNALS, not verdicts: SCF broad controls bundle loosely-related
refs (inflating scf_only); our finer semantic links need not route through one SCF
control (ours_only). Direction is ignored (SCF co-mapping is symmetric). See
validation/README.md for how to read and act on the three classes.

Run:
    python validation/scf_validate.py \
        --strm validation/out/scf_strm.json \
        --nodes out/caf_nodes.json --nodes out/iso27001_nodes.json \
        --nodes out/iso27002_nodes.json --nodes out/iso42001_nodes.json \
        --edges out/edges.json \
        --rollup CAF
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent


def load_nodes(paths: list[str]):
    idmap, reffw = {}, defaultdict(set)
    for p in paths:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        ns = d["nodes"] if isinstance(d, dict) else d
        for n in ns:
            fw, ref = n.get("framework"), n.get("native_ref")
            idmap[n["id"]] = (fw, ref)
            if fw and ref:
                reffw[fw].add(ref)
    return idmap, reffw


def canon(a, b):
    return tuple(sorted([a, b]))


def load_scf_comappings(strm_path: str):
    """Invert the STRM adapter's {framework: {scf_id: [{ref,...}]}} output into
    per-SCF-control {framework: set(refs)}, ready for co-mapping comparison."""
    strm = json.loads(Path(strm_path).read_text(encoding="utf-8"))
    by_control: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for fw, by_scf in strm.items():
        for scf_id, legs in by_scf.items():
            for leg in legs:
                ref = leg.get("ref")
                if ref:
                    by_control[scf_id][fw].add(ref)
    return strm, by_control


def rollup_ref(ref: str) -> str:
    """Truncate a ref at its final '.segment', e.g. 'A1.a' -> 'A1'. Used to
    compare frameworks whose target nodes are more granular than SCF's own
    mapping granularity (our CAF case rolled outcome-level A1.a up to
    principle-level A1)."""
    return ref.rsplit(".", 1)[0] if "." in ref else ref


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strm", default=str(HERE / "out" / "scf_strm.json"),
                     help="STRM json produced by scf_strm_adapter.py")
    ap.add_argument("--nodes", action="append", default=[], required=True,
                     metavar="PATH", help="repeatable: node json file(s) to load")
    ap.add_argument("--edges", default=str(HERE / "out" / "edges.json"),
                     help="our derived edges json ({'edges': [...]})")
    ap.add_argument("--rollup", action="append", default=[], metavar="FRAMEWORK",
                     help="repeatable, opt-in: framework(s) whose refs are "
                          "truncated at the final '.segment' for a granularity "
                          "roll-up view (e.g. --rollup CAF rolls A1.a up to A1)")
    ap.add_argument("--out-dir", default=str(HERE / "out"))
    ap.add_argument("--out-prefix", default="scf_validation_report")
    args = ap.parse_args()

    rollup_fws = set(args.rollup)

    strm, by_control = load_scf_comappings(args.strm)
    target = sorted(strm.keys())  # frameworks present in the STRM output
    pairs = list(itertools.combinations(target, 2))
    if not pairs:
        raise SystemExit("Need at least two frameworks in the STRM output "
                          "(re-run scf_strm_adapter.py with 2+ --map entries).")

    idmap, reffw = load_nodes(args.nodes)

    edges = json.loads(Path(args.edges).read_text(encoding="utf-8"))["edges"]
    ours = defaultdict(set)
    ours_rel = {}
    for e in edges:
        s, t = idmap.get(e["source"]), idmap.get(e["target"])
        if not s or not t or s[0] == t[0]:
            continue
        pr = canon(s, t)
        ours[canon(s[0], t[0])].add(pr)
        ours_rel[pr] = e.get("relation")

    # SCF-attested pairs + SCF coverage per framework
    scf_pairs = defaultdict(set)
    scf_cov = defaultdict(set)
    for scf_id, mappings in by_control.items():
        present = {fw: rs for fw, rs in mappings.items() if fw in target}
        for fw, rs in present.items():
            scf_cov[fw].update(rs)
        fws = list(present)
        for i in range(len(fws)):
            for j in range(i + 1, len(fws)):
                fa, fb = fws[i], fws[j]
                for ra in present[fa]:
                    for rb in present[fb]:
                        scf_pairs[canon((fa, ra), (fb, rb))].add(canon(fa, fb))

    # flatten scf pairs into per-fw-pair sets
    scf_by_fp = defaultdict(set)
    for pr, fps in scf_pairs.items():
        for fp in fps:
            scf_by_fp[fp].add(pr)

    lines = ["# SCF STRM external validation of derived crosswalk edges", "",
             f"Frameworks compared (from STRM output): {', '.join(target)}.",
             "",
             "SCF-attested co-mapping = two of our framework refs share one SCF control.",
             "Compared undirected, within the universe where both refs are our nodes AND "
             "SCF-covered. ours_only / scf_only are review signals, not errors "
             "(see script docstring and validation/README.md).", ""]

    # coverage sanity: overlap of our node refs with SCF-covered refs
    lines.append("## Framework coverage (our nodes vs SCF)\n")
    lines.append("| Framework | our nodes | SCF-covered refs | in both |")
    lines.append("|---|---|---|---|")
    for fw in target:
        both = reffw[fw] & scf_cov[fw]
        lines.append(f"| {fw} | {len(reffw[fw])} | {len(scf_cov[fw])} | {len(both)} |")
    lines.append("")

    summary = []
    for fp in [canon(a, b) for a, b in pairs]:
        fa, fb = fp
        our_set = ours.get(fp, set())
        scf_set = scf_by_fp.get(fp, set())

        def refs_ok(pr):  # both endpoints are real nodes in our graph
            return all(r in reffw[f] for f, r in pr)

        def scf_covered(pr):  # both endpoints SCF-covered
            return all(r in scf_cov[f] for f, r in pr)

        agreement = our_set & scf_set
        ours_only = {pr for pr in our_set if scf_covered(pr)} - scf_set
        scf_only = {pr for pr in scf_set if refs_ok(pr)} - our_set
        summary.append((fp, len(our_set), len(scf_set), len(agreement),
                        len(ours_only), len(scf_only)))

        lines.append(f"## {fa} <-> {fb}\n")
        lines.append(f"- our edges: **{len(our_set)}**  |  SCF co-maps (both our nodes): "
                     f"**{len({p for p in scf_set if refs_ok(p)})}**")
        rels = defaultdict(int)
        for pr in agreement:
            rels[ours_rel.get(pr, "?")] += 1
        rel_s = ", ".join(f"{k}:{v}" for k, v in sorted(rels.items()))
        lines.append(f"- **agreement: {len(agreement)}** ({rel_s})")
        lines.append(f"- ours_only (SCF-covered both, no SCF co-map): **{len(ours_only)}**")
        lines.append(f"- scf_only (both our nodes, we have no edge): **{len(scf_only)}**")

        # Optional granularity roll-up: some frameworks' target nodes are finer
        # grained (e.g. CAF outcome A1.a) than SCF's own mapping granularity
        # (e.g. CAF principle A1). Rolling up the frameworks named in --rollup
        # gives a fairer second view so that granularity mismatch alone does
        # not understate agreement. Opt-in per framework, off by default.
        active_rollup = rollup_fws & set(fp)
        if active_rollup:
            def roll(pr):
                return canon(*[(f, rollup_ref(r) if f in active_rollup else r)
                               for f, r in pr])
            r_our = {roll(p) for p in our_set}
            r_scf = {roll(p) for p in scf_set if refs_ok(p)}
            lines.append(f"- _rolled up ({', '.join(sorted(active_rollup))}, "
                         f"truncated at final '.segment')_: agreement "
                         f"**{len(r_our & r_scf)}**, ours_only {len(r_our - r_scf)}, "
                         f"scf_only {len(r_scf - r_our)}")

        def fmt(pr):
            (f1, r1), (f2, r2) = pr
            return f"{f1} {r1} - {f2} {r2}"

        for label, s in [("agreement", agreement), ("ours_only", ours_only),
                         ("scf_only", scf_only)]:
            ex = sorted(fmt(p) for p in s)[:8]
            if ex:
                lines.append(f"  - _{label} e.g._: " + "; ".join(ex))
        lines.append("")

    lines.insert(6, "## Summary\n")
    tbl = ["| pair | our edges | SCF co-maps | agreement | ours_only | scf_only |",
           "|---|---|---|---|---|---|"]
    for fp, no, ns, ag, oo, so in summary:
        tbl.append(f"| {fp[0]}<->{fp[1]} | {no} | {ns} | {ag} | {oo} | {so} |")
    for i, t in enumerate(tbl):
        lines.insert(7 + i, t)
    lines.insert(7 + len(tbl), "")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = out_dir / f"{args.out_prefix}.md"
    rep.write_text("\n".join(lines), encoding="utf-8")
    (out_dir / f"{args.out_prefix}.json").write_text(json.dumps(
        {"summary": [{"pair": f"{a}-{b}", "our": no, "scf": ns, "agree": ag,
                      "ours_only": oo, "scf_only": so}
                     for (a, b), no, ns, ag, oo, so in summary]}, indent=1),
        encoding="utf-8")

    print("Coverage (our / SCF / both):")
    for fw in target:
        print(f"  {fw:9} {len(reffw[fw]):4} / {len(scf_cov[fw]):4} / {len(reffw[fw] & scf_cov[fw]):4}")
    print("\nPair                 our  scf  agree  ours_only  scf_only")
    for fp, no, ns, ag, oo, so in summary:
        print(f"  {fp[0] + '<->' + fp[1]:20} {no:4} {ns:4}   {ag:4}     {oo:4}     {so:4}")
    print(f"\nWrote {rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
