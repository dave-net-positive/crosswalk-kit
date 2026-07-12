"""
embed.py - Build step 3: embed each mappable node's canonical_intent with a
local Ollama embedding model. No paid API; runs on whatever GPU or CPU is
hosting Ollama.

Design:
  - Embed canonical_intent, not raw_text - normalising idiom before vectorising is
    what pulls differently-worded requirements with the same intent closer together.
  - Symmetric instruction prompt: one identical instruction on every node, so all
    vectors live in a single instruction-conditioned space.
  - Batched requests to Ollama /api/embed (array input).
  - Cached by content hash (model + prompt version + instruction-wrapped text), so
    re-runs and added frameworks do not re-embed. Resumable: cache + nodes saved
    after every batch.

Endpoint: CROSSWALK_OLLAMA_URL env var or --url (default http://localhost:11434).

Run:
    python crosswalk/embed.py --in out/nodes.json --dry-run
    python crosswalk/embed.py --in out/nodes.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import httpx

from models import load_nodes, save_nodes

HERE = Path(__file__).parent
CACHE_PATH = HERE / "out" / "embed_cache.json"
DEFAULT_URL = os.environ.get("CROSSWALK_OLLAMA_URL", "http://localhost:11434")

MODEL = "qwen3-embedding:4b"   # default; override with --model
PROMPT_VERSION = "v1"
BATCH = 16
# Bump if the instruction changes, so the cache re-embeds rather than serving
# vectors from a different instruction-conditioned space.
INSTRUCTION = (
    "Instruct: Represent this security control by its underlying intent, so that "
    "controls expressing the same requirement are close together.\nQuery: "
)


def embed_input(canonical_intent: str, instruction: str = INSTRUCTION) -> str:
    return instruction + canonical_intent


def cache_key(text: str, model: str = MODEL) -> str:
    return hashlib.sha256(
        f"{model}\n{PROMPT_VERSION}\n{text}".encode("utf-8")
    ).hexdigest()


def load_cache() -> dict[str, list[float]]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, list[float]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def embed_batch(client: httpx.Client, url: str, inputs: list[str],
                model: str = MODEL) -> list[list[float]]:
    # Generous timeout + one retry: the first batch can include the model load,
    # which under GPU contention (another model resident in VRAM) takes minutes.
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = client.post(
                f"{url}/api/embed", json={"model": model, "input": inputs},
                timeout=600.0,
            )
            resp.raise_for_status()
            return resp.json()["embeddings"]
        except httpx.TimeoutException as exc:
            last_exc = exc
            print(f"  batch timed out (attempt {attempt + 1}/2); retrying...")
    raise last_exc  # type: ignore[misc]


def main() -> int:
    ap = argparse.ArgumentParser(description="Embed canonical_intent via Ollama.")
    ap.add_argument("--in", dest="in_path", required=True, help="node file to embed")
    ap.add_argument("--out", dest="out_path", default=None)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--force", action="store_true", help="re-embed even if cached")
    ap.add_argument("--dry-run", action="store_true", help="report; no requests")
    ap.add_argument("--model", default=MODEL,
                    help="embedding model (vectors from different models are "
                         "NOT comparable - re-embed the whole corpus to switch)")
    ap.add_argument("--instruction", default=INSTRUCTION,
                    help="prompt prefix; pass '' for models that want bare text")
    args = ap.parse_args()
    model = args.model
    instr = args.instruction

    in_path = Path(args.in_path)
    out_path = Path(args.out_path) if args.out_path else in_path
    nodes = load_nodes(in_path)

    targets = [n for n in nodes if n.is_mappable() and n.canonical_intent]
    skipped = sum(1 for n in nodes if n.is_mappable() and not n.canonical_intent)
    cache = load_cache()

    from_cache = 0
    todo = []
    for n in targets:
        key = cache_key(embed_input(n.canonical_intent, instr), model)
        if key in cache and not args.force:
            vec = cache[key]
            n.embedding, n.embedding_model, n.embedding_dim = vec, model, len(vec)
            from_cache += 1
        else:
            todo.append(n)

    n_batches = (len(todo) + args.batch - 1) // args.batch
    print(f"Endpoint: {args.url}  model: {model}")
    print(f"Mappable with canonical_intent: {len(targets)} "
          f"({skipped} skipped for missing intent)")
    print(f"Embeddings: {from_cache} from cache, {len(todo)} to request "
          f"({n_batches} batches of <= {args.batch})")

    if args.dry_run:
        print("\nDry run: no requests made.")
        return 0
    if not todo:
        save_nodes(nodes, out_path)
        print("Nothing to do; all targets already embedded.")
        return 0

    done = 0
    dims: set[int] = set()
    with httpx.Client() as client:
        # Fail fast with a clear message if the endpoint is unreachable.
        try:
            client.get(f"{args.url}/api/tags", timeout=10.0).raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Cannot reach Ollama at {args.url}: {exc}")
            return 2
        for i, batch in enumerate(batched(todo, args.batch), start=1):
            inputs = [embed_input(n.canonical_intent, instr) for n in batch]
            vectors = embed_batch(client, args.url, inputs, model)
            for node, vec in zip(batch, vectors):
                node.embedding, node.embedding_model = vec, model
                node.embedding_dim = len(vec)
                dims.add(len(vec))
                cache[cache_key(embed_input(node.canonical_intent, instr), model)] = vec
                done += 1
            save_cache(cache)
            save_nodes(nodes, out_path)
            print(f"  batch {i}/{n_batches} done ({done}/{len(todo)} nodes)")

    print(f"\nEmbedded {done} nodes -> {out_path}; dims: {sorted(dims)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
