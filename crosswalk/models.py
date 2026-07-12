"""
models.py - Node model for the control-crosswalk knowledge graph.

A Node is one atomic unit from a framework: an objective, principle, outcome,
control, clause, requirement, assertion, evidence item or policy statement -
whatever the smallest thing is that a framework adapter wants to canonicalise,
embed and match. Every framework adapter emits Nodes in this one shape, so all
downstream stages (canonicalisation, embedding, candidate generation,
adjudication, Neo4j load) stay framework-agnostic.

framework and node_type are plain strings, not enums: adapters mint whatever
vocabulary their framework needs (e.g. "control", "outcome", "clause",
"policy_statement") without touching this module. MAPPABLE_TYPES is the one
piece of shared vocabulary - the node types that are crosswalk targets (get
canonicalised, embedded, matched). Structural containers (e.g. an "objective"
or "document" that only exists to group children) are kept for hierarchy and
roll-up but are not themselves matched; adapters extend MAPPABLE_TYPES with
their own leaf types as needed.

Stdlib only, no third-party dependencies.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# Node types that are crosswalk targets by default. Adapters may add their own
# leaf types, e.g. MAPPABLE_TYPES.add("igp") or MAPPABLE_TYPES.update({"assertion",
# "evidence"}). Structural/grouping types should be left out.
MAPPABLE_TYPES: set[str] = {
    "outcome", "control", "clause", "requirement", "principle", "statement",
}


@dataclass
class Node:
    id: str
    framework: str
    version: str
    native_ref: str
    node_type: str
    title: str
    raw_text: str
    canonical_intent: Optional[str] = None
    # Filled by later pipeline stages, None until then.
    parent_id: Optional[str] = None
    # Adapter-specific metadata, e.g. {"doc": "policy-042"} for same-document
    # blocking, or {"igp_status": "achieved"} for a CAF-style adapter.
    extra: dict[str, Any] = field(default_factory=dict)
    embedding: Optional[list[float]] = None
    embedding_model: Optional[str] = None
    embedding_dim: Optional[int] = None

    def is_mappable(self) -> bool:
        return self.node_type in MAPPABLE_TYPES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Node":
        return cls(**d)


def make_id(framework: str, version: str, native_ref: str) -> str:
    """Stable node id: framework:version:native_ref, e.g. CAF:4.0:A1.a."""
    return f"{framework}:{version}:{native_ref}"


def save_nodes(nodes: Iterable[Node], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"nodes": [n.to_dict() for n in nodes]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_nodes(path: str | Path) -> list[Node]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Accept both the standard {"nodes": [...]} shape and a bare array, so
    # hand-written or third-party node files load without ceremony.
    records = data["nodes"] if isinstance(data, dict) else data
    return [Node.from_dict(d) for d in records]


def validate_nodes(nodes: list[Node]) -> list[str]:
    """Return a list of integrity problems; an empty list means the set is clean."""
    problems: list[str] = []

    counts = Counter(n.id for n in nodes)
    for nid, c in counts.items():
        if c > 1:
            problems.append(f"duplicate id: {nid} ({c} times)")

    idset = set(counts)
    for n in nodes:
        if n.parent_id is not None and n.parent_id not in idset:
            problems.append(f"{n.id}: dangling parent_id {n.parent_id}")
        if not n.title.strip():
            problems.append(f"{n.id}: empty title")
        if not n.raw_text.strip():
            problems.append(f"{n.id}: empty raw_text")
    return problems
