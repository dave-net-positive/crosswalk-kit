"""
caf_adapter.py - Turn the NCSC Cyber Assessment Framework (CAF) into Nodes.

CAF is Crown copyright, published under the Open Government Licence v3.0, so
its content may be reproduced and adapted with attribution. Source: NCSC Cyber
Assessment Framework 4.0, https://www.ncsc.gov.uk/collection/cyber-assessment-framework

Hierarchy emitted (via parent_id on each Node):
    objective -> principle -> contributing outcome -> IGP

Two ingestion paths:
  - from_json(path): load a structured CAF data file. Clean and deterministic;
    supports the full hierarchy including IGPs.
  - from_ncsc_pdf(path): parse the official CAF 4.0 PDF directly, so the source
    of truth is the published document with no hand-transcription. This pass
    extracts the structural layer (objectives, principles, contributing
    outcomes and their descriptions) - a good first-pass crosswalk
    granularity. IGP-table extraction is a deliberate follow-on: see the note
    on parse_caf_text() below. Use from_json() when you have (or have built)
    structured IGP data instead.

Run:
    python crosswalk/adapters/caf_adapter.py path/to/caf_4_0.pdf out/caf_nodes.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Same import fallback as every other adapter in this kit - see
# template_adapter.py for why both branches are needed.
try:
    from ..models import MAPPABLE_TYPES, Node, make_id, save_nodes
except ImportError:  # pragma: no cover - direct-script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from models import MAPPABLE_TYPES, Node, make_id, save_nodes

FRAMEWORK = "CAF"

# "outcome" is already a default mappable type in models.py; "igp" (the finer
# indicator-of-good-practice granularity, only produced by from_json()) is
# CAF-specific, so this adapter registers it itself, per the convention
# described in models.py and template_adapter.py. "objective" and "principle"
# are deliberately NOT added here - they're structural roll-up levels, not
# crosswalk match targets.
MAPPABLE_TYPES.add("igp")


# ---------------------------------------------------------------------------
# Structured JSON path
# ---------------------------------------------------------------------------
def from_json(path: str | Path, version: str = "4.0") -> list[Node]:
    """Build Nodes from a structured CAF data file.

    Expected shape:
        {
          "version": "4.0",
          "objectives": [
            {"ref": "A", "title": "...", "description": "...",
             "principles": [
               {"ref": "A1", "title": "...", "description": "...",
                "outcomes": [
                  {"ref": "A1.a", "title": "...", "description": "...",
                   "igps": {"not_achieved": [...], "partially_achieved": [...],
                            "achieved": [...]}}
                ]}
             ]}
          ]
        }
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("version", version)
    nodes: list[Node] = []

    def add(native_ref, node_type, title, raw_text, parent_id, extra=None):
        node = Node(
            id=make_id(FRAMEWORK, version, native_ref),
            framework=FRAMEWORK,
            version=version,
            native_ref=native_ref,
            node_type=node_type,
            title=title.strip(),
            raw_text=(raw_text or title).strip(),
            parent_id=parent_id,
            extra=extra or {},
        )
        nodes.append(node)
        return node.id

    for obj in data["objectives"]:
        obj_id = add(obj["ref"], "objective", obj["title"],
                     obj.get("description", ""), None)
        for pr in obj.get("principles", []):
            pr_id = add(pr["ref"], "principle", pr["title"],
                        pr.get("description", ""), obj_id)
            for oc in pr.get("outcomes", []):
                oc_id = add(oc["ref"], "outcome", oc["title"],
                            oc.get("description", ""), pr_id)
                for status, items in (oc.get("igps") or {}).items():
                    for i, text in enumerate(items, start=1):
                        ref = f"{oc['ref']}#{status}#{i}"
                        add(ref, "igp", f"{oc['ref']} IGP ({status} {i})",
                            text, oc_id, extra={"igp_status": status})
    return nodes


# ---------------------------------------------------------------------------
# Official PDF path
# ---------------------------------------------------------------------------
# The Objective C heading uses an en dash (U+2013) as its separator where the
# others use a hyphen, so the class covers hyphen, en dash and em dash.
_DASH = "-–—"
_OBJ = re.compile(rf"^CAF\s*[{_DASH}]\s*Objective\s+([A-D])\s*[{_DASH}]\s*(.+)$")
_PRIN = re.compile(r"^Principle\s+([A-D]\d+)\s+(.+)$")
_OUT = re.compile(r"^([A-D]\d+\.[a-z])\s+(.+)$")
_TABLE = re.compile(r"^(Not Achieved|Partially Achieved|Achieved)\b")
# Page furniture and running headers that interleave the body text.
_NOISE = re.compile(r"^(National Cyber Security Centre|The Cyber Assessment Framework|\d+)\s*$")
# Table-of-contents lines: a dotted leader followed by a page number.
_LEADER = re.compile(r"\.{4,}\s*\d+\s*$")
_TABLE_ROW = ("At least one of the following", "All the following")
# Start of the document's licence/copyright back-matter on the final page; the
# footer wraps across several lines, so everything from here on is dropped.
_FOOTER_START = "© Crown copyright"


def _clean(text: str) -> str:
    """Collapse the runs of whitespace and stray newlines that pypdf leaves
    between wrapped lines, so titles and descriptions are single clean
    strings.

    CAF text uses genuine en dashes (U+2013) and curly apostrophes (U+2019);
    these are kept verbatim, as raw_text is the source of truth for review.

    Also rejoins a single capital letter split off the front of its word by
    the PDF extraction (the D1.a heading extracts as 'R esponse Plan').
    """
    text = re.sub(r"\s+", " ", text).strip()
    # 'A' and 'I' are real one-letter words ("A risk...") - never rejoined.
    return re.sub(r"\b([B-HJ-Z]) (?=[a-z]{2})", r"\1", text)


def from_ncsc_pdf(path: str | Path, version: str = "4.0") -> list[Node]:
    """Parse the official CAF PDF into Nodes. Requires pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("from_ncsc_pdf needs pypdf: pip install pypdf") from exc
    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    return parse_caf_text(text, version=version)


def parse_caf_text(text: str, version: str = "4.0") -> list[Node]:
    """Extract objectives, principles and contributing outcomes (with their
    descriptions) from CAF PDF-extracted text.

    Written against the CAF 4.0 text structure: an objective heading
    ('CAF - Objective A - ...'), principle headings ('Principle A1 ...'),
    outcome headings ('A1.a ...') each followed by a short description, then
    an IGP table introduced by a 'Not Achieved' / 'Achieved' header row.

    IGP rows are intentionally not split into nodes here: doing that reliably
    needs validation against the multi-column IGP tables in a specific PDF
    build, and contributing outcomes are a reasonable crosswalk granularity
    to start from. Use from_json() when you have structured IGP data.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    # Cut the licence/copyright back-matter off the end of the final page.
    for i, ln in enumerate(lines):
        if ln.startswith(_FOOTER_START):
            lines = lines[:i]
            break
    # Drop blanks, page furniture, and table-of-contents leader lines. Removing
    # the ToC is what stops objectives/principles being emitted twice.
    lines = [
        ln for ln in lines
        if ln and not _NOISE.match(ln) and not _LEADER.search(ln)
    ]

    nodes: list[Node] = []
    desc: dict[str, list[str]] = {}
    seen: set[str] = set()
    cur_obj = cur_prin = None
    target: Node | None = None
    in_table = False

    def new_node(ref, ntype, title, parent):
        node = Node(
            id=make_id(FRAMEWORK, version, ref),
            framework=FRAMEWORK, version=version, native_ref=ref,
            node_type=ntype, title=_clean(title), raw_text="",
            parent_id=parent,
        )
        nodes.append(node)
        seen.add(node.id)
        desc[node.id] = []
        return node

    for ln in lines:
        m = _OBJ.match(ln)
        if m and make_id(FRAMEWORK, version, m.group(1)) not in seen:
            cur_obj = new_node(m.group(1), "objective", m.group(2), None)
            cur_prin, target, in_table = None, cur_obj, False
            continue
        m = _PRIN.match(ln)
        if m and make_id(FRAMEWORK, version, m.group(1)) not in seen:
            parent = cur_obj.id if cur_obj else None
            cur_prin = new_node(m.group(1), "principle", m.group(2), parent)
            target, in_table = cur_prin, False
            continue
        # Outcome headings must be detected even inside an IGP table, because
        # each contributing outcome is followed by its own table before the
        # next outcome heading appears (A1.a table ... then A1.b ...).
        m = _OUT.match(ln)
        if m and make_id(FRAMEWORK, version, m.group(1)) not in seen:
            parent = cur_prin.id if cur_prin else None
            target = new_node(m.group(1), "outcome", m.group(2), parent)
            in_table = False
            continue
        if _TABLE.match(ln) or ln.startswith(_TABLE_ROW):
            in_table = True
            continue
        if target is not None and not in_table:
            desc[target.id].append(ln)

    for node in nodes:
        body = _clean(" ".join(desc[node.id]))
        # A few outcome titles wrap across a line break mid-parenthesis (e.g.
        # C1.f '... (within' + 'Security Monitoring) ...'). If the title has
        # an unclosed '(', reunite it with the closing ')' from the body.
        if node.title.count("(") > node.title.count(")") and ")" in body:
            head, _, rest = body.partition(")")
            node.title = _clean(f"{node.title} {head})")
            body = _clean(rest)
        node.raw_text = body if body else node.title
    return nodes


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            f"usage: python {Path(__file__).name} <caf_4_0.pdf> <out_nodes.json>"
        )
    caf_nodes = from_ncsc_pdf(sys.argv[1])
    save_nodes(caf_nodes, sys.argv[2])
    print(f"Wrote {len(caf_nodes)} nodes -> {sys.argv[2]}")
