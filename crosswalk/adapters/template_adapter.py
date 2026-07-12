"""
template_adapter.py - Skeleton adapter: parse plain text into Nodes.

Copy this file as the starting point for a new framework adapter. It parses a
trivial two-column text format purely to be a *working, runnable* example -
your real source will more likely be a PDF, a spreadsheet export, or a folder
of Word documents, but the shape an adapter produces never changes: a flat
list of Node objects, written to disk with models.save_nodes().

Format this adapter understands (see DEMO_TEXT below for a full example):

    # Section title
    NATIVE-REF<TAB>Requirement text goes here.
    NATIVE-REF<TAB>Another requirement in the same section.

    # Another section title
    NATIVE-REF<TAB>A requirement in the second section.

- A line starting with "# " opens a new section (a structural node).
- Any other non-blank line is "native_ref<TAB>text" (or native_ref followed by
  2+ spaces then text) - a leaf requirement, belonging to the section above it.

Read this file top-to-bottom before writing your own adapter; the comments
below walk through every decision an adapter has to make. Nothing here is
framework-specific - swap the parsing logic for whatever your source needs
and keep the Node-construction conventions.

Run:
    python crosswalk/adapters/template_adapter.py                 # parses the
                                                                    # built-in
                                                                    # demo text
                                                                    # and prints
                                                                    # a summary
    python crosswalk/adapters/template_adapter.py in.txt out.json  # parses a
                                                                    # real file
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# -----------------------------------------------------------------------------
# Import Node / make_id / save_nodes from the shared model.
#
# The try/except lets this file work two ways:
#   - as part of the package: `from crosswalk.adapters import template_adapter`
#   - run directly as a script: `python crosswalk/adapters/template_adapter.py`,
#     which is handy while you're still iterating on a new adapter and don't
#     want to worry about how it's invoked.
# Every adapter in this kit uses this same fallback - copy it verbatim.
# -----------------------------------------------------------------------------
try:
    from ..models import Node, make_id, save_nodes
except ImportError:  # pragma: no cover - direct-script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from models import Node, make_id, save_nodes


# -----------------------------------------------------------------------------
# 1. Framework identifier and version.
#
# `framework` and `version` are plain strings - this kit has no fixed enum of
# frameworks (see crosswalk/models.py) - so use whatever is stable and
# human-readable. House convention: a short upper-case acronym for the
# framework, a dotted version/edition string for the release.
# -----------------------------------------------------------------------------
FRAMEWORK = "TEMPLATE"
VERSION = "1.0"


# -----------------------------------------------------------------------------
# 2. node_type choice, and MAPPABLE_TYPES registration.
#
# node_type is also a plain string; there's no registry you must extend just
# to invent a new one. What DOES matter is MAPPABLE_TYPES in crosswalk/models.py
# - only node_types listed there get canonicalised, embedded and matched by
# the rest of the pipeline. Structural/container types (a section, a chapter,
# a document...) are deliberately left out of MAPPABLE_TYPES: they exist so
# parent_id can build a hierarchy and results can roll up to them, but a
# section heading is not itself a requirement, so it should never be a
# crosswalk match target.
#
# This template emits two node_types:
#   "section"     - a structural heading. NOT mappable.
#   "requirement" - a leaf requirement. IS mappable - it's already in the
#                   default MAPPABLE_TYPES set in models.py, so nothing extra
#                   to do here. If your own adapter invents a node_type name
#                   that isn't already covered, register it once, e.g.:
#                       from ..models import MAPPABLE_TYPES
#                       MAPPABLE_TYPES.add("my_new_type")
#                   (or just reuse an existing name - "clause", "control",
#                   "standard" - if your granularity matches one already there)
# -----------------------------------------------------------------------------
SECTION_TYPE = "section"
REQUIREMENT_TYPE = "requirement"

_SECTION = re.compile(r"^#\s*(.+)$")
# Two columns: a native ref, then either a tab or 2+ spaces, then the text.
_ROW = re.compile(r"^(\S+)(?:\t+|[ ]{2,})(.+)$")

DEMO_TEXT = """\
# Access control
AC-1\tRestrict system access to authorised users only.
AC-2\tEnforce unique user identifiers; shared credentials are prohibited.

# Logging and monitoring
LOG-1\tRecord all privileged access events with an accurate timestamp.
LOG-2\tRetain access logs for a minimum of twelve months.
"""


def parse_template_text(text: str, framework: str = FRAMEWORK,
                        version: str = VERSION) -> list[Node]:
    """Parse the two-column demo format (see module docstring) into Nodes.

    Every requirement row is attached, via parent_id, to the most recently
    seen section heading. That's the general CONTAINS-hierarchy pattern every
    adapter in this kit follows: a child's parent_id is the id of whatever
    node structurally contains it (objective -> principle -> outcome for CAF,
    document -> policy_statement for an internal policy corpus, chapter ->
    section -> requirement here).
    """
    nodes: list[Node] = []
    current_section_id: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        sec = _SECTION.match(line)
        if sec:
            title = sec.group(1).strip()
            # -----------------------------------------------------------
            # id convention: "<FRAMEWORK>:<version>:<native_ref>".
            # native_ref is whatever identifier your source itself uses - a
            # clause number, a control code, a paragraph reference. Use it
            # VERBATIM; don't invent your own numbering. This demo format
            # has no separate code for a section, so we slugify the title
            # as a fallback - real frameworks almost always give you a real
            # reference to use instead.
            # -----------------------------------------------------------
            native_ref = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").upper()
            node = Node(
                id=make_id(framework, version, native_ref),
                framework=framework,
                version=version,
                native_ref=native_ref,
                node_type=SECTION_TYPE,
                title=title,
                # raw_text vs canonical_intent, decided once here for every
                # node type this adapter emits:
                #   raw_text          - the verbatim (or lightly cleaned)
                #                       source text. This is what a human
                #                       reviewer checks a proposed match
                #                       against - never paraphrase it away,
                #                       even for a heading like this one.
                #   canonical_intent  - deliberately NOT set here. It is
                #                       authored LATER, by the LLM
                #                       canonicalisation pipeline stage (not
                #                       this adapter), in a fixed
                #                       "requires: X; in order to: Y." style.
                #                       Every adapter leaves it as None -
                #                       that's the whole point of having a
                #                       separate stage: one consistent voice
                #                       across every framework, however
                #                       differently each one is worded.
                raw_text=title,
                parent_id=None,  # sections in this demo are top-level; a
                                 # deeper source would thread a parent id
                                 # through as it descends (part -> chapter ->
                                 # section), exactly like the leaf case below
            )
            nodes.append(node)
            current_section_id = node.id
            continue

        row = _ROW.match(line)
        if row:
            native_ref, body = row.group(1).strip(), row.group(2).strip()
            nodes.append(Node(
                id=make_id(framework, version, native_ref),
                framework=framework,
                version=version,
                native_ref=native_ref,
                node_type=REQUIREMENT_TYPE,
                title=body if len(body) <= 80 else body[:77] + "...",
                raw_text=body,
                parent_id=current_section_id,
                # ---------------------------------------------------------
                # extra{} is a free-form metadata bag later pipeline stages
                # can read; adapters are free to put whatever they need in
                # it. The one nearly every adapter ends up wanting is "doc":
                # which source document a node came from, so candidate
                # generation (crosswalk/candidates.py, --intra mode) can
                # skip same-document pairs when matching a large corpus
                # against itself (two requirements from the SAME internal
                # policy are not an interesting "match" - they're the same
                # document referencing itself). A framework ingested whole
                # from one file can hardcode a single doc value like this;
                # a multi-document corpus sets it per source file instead.
                # ---------------------------------------------------------
                extra={"doc": f"{framework.lower()}-{version}"},
            ))
            continue

        # Anything else (stray commentary, a line the regexes don't match)
        # is silently skipped here. A production adapter should usually
        # collect and report unparsed lines instead, so gaps in extraction
        # are visible rather than silently dropped - see caf_adapter.py's
        # noise-filtering for a fuller example of that discipline.

    return nodes


if __name__ == "__main__":
    if len(sys.argv) == 1:
        demo_nodes = parse_template_text(DEMO_TEXT)
        print(f"Parsed {len(demo_nodes)} nodes from the built-in demo text:\n")
        for n in demo_nodes:
            marker = "  " if n.parent_id else ""
            print(f"{marker}{n.id:<28} [{n.node_type}] {n.title}")
        print("\nRun with two paths to parse a real file:\n"
              "  python crosswalk/adapters/template_adapter.py in.txt out.json")
    elif len(sys.argv) == 3:
        src_path, out_path = sys.argv[1], sys.argv[2]
        real_nodes = parse_template_text(Path(src_path).read_text(encoding="utf-8"))
        save_nodes(real_nodes, out_path)
        print(f"Wrote {len(real_nodes)} nodes -> {out_path}")
    else:
        raise SystemExit(
            f"usage: python {Path(__file__).name} [<source.txt> <out_nodes.json>]"
        )
