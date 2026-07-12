"""
api_adjudicator.py - Adjudicate candidate pairs via a direct chat-completion
API call (the non-agent path for pipeline stage 5, "Adjudicate").

The agent-driven path (see docs/AGENT_ADJUDICATION.md) has a coding assistant
read batches interactively. This script does the same job non-interactively:
it reads one or more batch files written by the candidates stage (each a JSON
array of pairs - a_id/a_framework/a_ref/a_title/a_canonical_intent/a_text,
mirrored for b, plus a cosine score), loads the adjudication rubric, and sends
one chat-completion request per batch to either the Anthropic Messages API or
an OpenAI-compatible /chat/completions endpoint (which also covers local
servers that speak the OpenAI API, not just openai.com). The model is
instructed to return exactly one JSONL judgment per pair; the reply is parsed
defensively, each judgment is validated against the schema below, and the
result is written to out_<batchstem>.json next to the batch file.

Judgment schema (one JSON object per line in the model's reply):
    {"source_fw": str, "source_ref": str,
     "target_fw": str, "target_ref": str,
     "relation": "EQUIVALENT" | "PARTIAL" | "SUPPORTS" | "INFORMS" | "no_relation",
     "confidence": float in [0, 1],
     "rationale": str}

For EQUIVALENT/PARTIAL the source/target order doesn't matter; for
SUPPORTS/INFORMS, source is the requirement that supports/informs the target.
no_relation lines are written and dropped downstream: this script writes
every pair's verdict, typed or not, so batches keep 1:1 pair accounting for
audit; merge_judgments.py is what discards the no_relation lines (matched
case-insensitively) before anything becomes an edge - gaps are computed at
query time, never stored.

Environment:
    ANTHROPIC_API_KEY   required for --provider anthropic (the default)
    OPENAI_API_KEY      required for --provider openai
    OPENAI_BASE_URL     optional, default https://api.openai.com/v1 - point
                        this at a local OpenAI-compatible server instead

Keys are read from the environment only. Never pass a key on the command
line, and this script never prints or logs one.

Run:
    # Anthropic (default model claude-sonnet-4-5)
    export ANTHROPIC_API_KEY=...
    uv run python crosswalk/api_adjudicator.py crosswalk/out/adjudication/batch_001.json

    # OpenAI, or a local OpenAI-compatible server
    export OPENAI_API_KEY=...
    uv run python crosswalk/api_adjudicator.py --provider openai --model gpt-4o \
        crosswalk/out/adjudication/batch_001.json crosswalk/out/adjudication/batch_002.json

    # Point --provider openai at a local server instead of openai.com
    export OPENAI_API_KEY=local-only-placeholder
    export OPENAI_BASE_URL=http://localhost:11434/v1
    uv run python crosswalk/api_adjudicator.py --provider openai --model llama3.1 \
        crosswalk/out/adjudication/batch_001.json

    # See the assembled prompt for the first batch without spending a call
    uv run python crosswalk/api_adjudicator.py --dry-run crosswalk/out/adjudication/batch_001.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
DEFAULT_RUBRIC = HERE / "RUBRIC.md"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

TEMPERATURE = 0
MAX_TOKENS = 4096
REQUEST_TIMEOUT = 120.0
MAX_ATTEMPTS = 3  # total attempts per request, exponential backoff between them
BACKOFF_BASE_SECONDS = 1.0

RELATIONS = {"EQUIVALENT", "PARTIAL", "SUPPORTS", "INFORMS", "no_relation"}
REQUIRED_FIELDS = ("source_fw", "source_ref", "target_fw", "target_ref",
                    "relation", "confidence", "rationale")


# --- prompt assembly --------------------------------------------------------

def build_prompt(rubric: str, pairs: list[dict]) -> str:
    """Assemble the single-turn prompt for one batch: rubric + pairs + a
    strict output-format instruction. Kept as one string so both providers
    (and --dry-run) share exactly the same text."""
    pairs_json = json.dumps(pairs, indent=2, ensure_ascii=False)
    return (
        f"{rubric.strip()}\n\n"
        "## Pairs to adjudicate\n\n"
        "Each pair below carries both sides' framework, native reference, "
        "title, canonical intent, cosine score and verbatim source text "
        "(a_text/b_text). Judge the verbatim text, not only the canonical "
        "intent.\n\n"
        f"```json\n{pairs_json}\n```\n\n"
        "## Output format\n\n"
        "Output ONLY JSONL: exactly one JSON object per line, one line per "
        "pair above, no other text, no markdown code fences, no commentary "
        "before or after. Each line must be a single JSON object with "
        "exactly these fields:\n\n"
        '  {"source_fw": <framework of the side that supports/informs the '
        "other, or either side's framework for a symmetric relation>,\n"
        '   "source_ref": <that side\'s native reference>,\n'
        '   "target_fw": <the other side\'s framework>,\n'
        '   "target_ref": <the other side\'s native reference>,\n'
        '   "relation": one of "EQUIVALENT", "PARTIAL", "SUPPORTS", '
        '"INFORMS", "no_relation",\n'
        '   "confidence": a float between 0 and 1,\n'
        '   "rationale": a short (1-3 sentence) justification grounded in '
        "the verbatim text}\n\n"
        'Use "no_relation" for pairs that do not meet the rubric\'s bar for '
        "any typed relation - do not omit a pair, and do not invent a "
        "relation to avoid saying no_relation. For EQUIVALENT/PARTIAL, "
        "source/target order does not matter; for SUPPORTS/INFORMS, source "
        "is the requirement that supports/informs the target."
    )


# --- HTTP with retries ------------------------------------------------------

def post_with_retries(url: str, headers: dict, body: dict) -> httpx.Response:
    """POST with up to MAX_ATTEMPTS tries, exponential backoff on 429/5xx/
    timeouts. Anything else (4xx other than 429) fails immediately."""
    delay = BACKOFF_BASE_SECONDS
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == MAX_ATTEMPTS:
                raise SystemExit(f"Request failed after {MAX_ATTEMPTS} attempts: {exc}")
            print(f"  retry {attempt}/{MAX_ATTEMPTS} after {exc!r}; "
                  f"waiting {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_ATTEMPTS:
                raise SystemExit(f"Request failed after {MAX_ATTEMPTS} attempts: "
                                  f"HTTP {resp.status_code}: {resp.text[:500]}")
            print(f"  retry {attempt}/{MAX_ATTEMPTS} after HTTP "
                  f"{resp.status_code}; waiting {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
            continue

        resp.raise_for_status()
        return resp

    raise SystemExit(f"Request failed after {MAX_ATTEMPTS} attempts")


def call_anthropic(prompt: str, model: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set. Export it and retry - "
                          "never pass a key on the command line.")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = post_with_retries(ANTHROPIC_URL, headers, body)
    data = resp.json()
    parts = data.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def call_openai(prompt: str, model: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set. Export it and retry - "
                          "never pass a key on the command line.")
    base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL).rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = post_with_retries(f"{base_url}/chat/completions", headers, body)
    data = resp.json()
    return data["choices"][0]["message"]["content"]


# --- defensive response parsing + validation --------------------------------

def parse_jsonl(text: str) -> list[dict]:
    """Turn the model's reply into a list of raw (unvalidated) judgment
    dicts. Tolerates a wrapping ``` fence and stray per-line fences/commas;
    any line that still isn't a JSON object is skipped with a warning."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    judgments = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip().strip("`").rstrip(",")
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"  warning: reply line {i} is not valid JSON, skipping "
                  f"({exc}): {line[:200]!r}", file=sys.stderr)
            continue
        if not isinstance(obj, dict):
            print(f"  warning: reply line {i} is not a JSON object, "
                  f"skipping: {line[:200]!r}", file=sys.stderr)
            continue
        judgments.append(obj)
    return judgments


def validate_judgment(obj: dict, line_no: int) -> dict | None:
    """Validate one raw judgment against the schema. Returns a clean dict
    with exactly the schema fields, or None (with a warning) if it fails."""
    for field in REQUIRED_FIELDS:
        if field not in obj:
            print(f"  warning: judgment {line_no} missing {field!r}, "
                  f"skipping: {obj}", file=sys.stderr)
            return None

    relation = obj["relation"]
    if relation not in RELATIONS:
        print(f"  warning: judgment {line_no} has unknown relation "
              f"{relation!r}, skipping", file=sys.stderr)
        return None

    for field in ("source_fw", "source_ref", "target_fw", "target_ref", "rationale"):
        if not isinstance(obj[field], str) or not obj[field].strip():
            print(f"  warning: judgment {line_no} field {field!r} is not a "
                  f"non-empty string, skipping", file=sys.stderr)
            return None

    try:
        confidence = float(obj["confidence"])
    except (TypeError, ValueError):
        print(f"  warning: judgment {line_no} has non-numeric confidence "
              f"{obj['confidence']!r}, skipping", file=sys.stderr)
        return None
    if not (0.0 <= confidence <= 1.0):
        clamped = max(0.0, min(1.0, confidence))
        print(f"  warning: judgment {line_no} confidence {confidence} out "
              f"of [0, 1], clamping to {clamped}", file=sys.stderr)
        confidence = clamped

    return {
        "source_fw": obj["source_fw"],
        "source_ref": obj["source_ref"],
        "target_fw": obj["target_fw"],
        "target_ref": obj["target_ref"],
        "relation": relation,
        "confidence": confidence,
        "rationale": obj["rationale"],
    }


# --- batch processing --------------------------------------------------------

def process_batch(batch_path: Path, rubric: str, provider: str, model: str) -> None:
    pairs = json.loads(batch_path.read_text(encoding="utf-8"))
    if not isinstance(pairs, list):
        raise SystemExit(f"{batch_path}: expected a JSON array of pairs")

    prompt = build_prompt(rubric, pairs)
    print(f"{batch_path.name}: {len(pairs)} pairs -> {provider}/{model}")

    reply = call_anthropic(prompt, model) if provider == "anthropic" else call_openai(prompt, model)

    raw = parse_jsonl(reply)
    judgments = []
    for i, obj in enumerate(raw, 1):
        v = validate_judgment(obj, i)
        if v is not None:
            judgments.append(v)

    out_path = batch_path.parent / f"out_{batch_path.stem}.json"
    out_path.write_text(json.dumps(judgments, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    typed = sum(1 for j in judgments if j["relation"] != "no_relation")
    print(f"  reply: {len(raw)} lines parsed, {len(judgments)} valid "
          f"judgments ({typed} typed, {len(judgments) - typed} no_relation) "
          f"-> {out_path}")
    if len(judgments) != len(pairs):
        print(f"  NOTE: {len(pairs)} pairs in but {len(judgments)} valid "
              f"judgments out - check warnings above", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Adjudicate candidate pairs via a direct chat-completion "
                    "API call (batch files as produced by the candidates stage).")
    ap.add_argument("batches", nargs="+",
                    help="one or more batch JSON files to adjudicate")
    ap.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic",
                    help="which chat-completion API to call (default: anthropic)")
    ap.add_argument("--model", default=None,
                    help=f"model name (default: {DEFAULT_ANTHROPIC_MODEL} for "
                         f"--provider anthropic, {DEFAULT_OPENAI_MODEL} for "
                         "--provider openai; override to match a local "
                         "OpenAI-compatible server's model name)")
    ap.add_argument("--rubric", default=str(DEFAULT_RUBRIC),
                    help="path to the adjudication rubric "
                         f"(default: {DEFAULT_RUBRIC})")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the assembled prompt for the first batch and exit")
    args = ap.parse_args()

    rubric_path = Path(args.rubric)
    if not rubric_path.exists():
        raise SystemExit(f"Rubric not found: {rubric_path}")
    rubric = rubric_path.read_text(encoding="utf-8")

    model = args.model or (DEFAULT_ANTHROPIC_MODEL if args.provider == "anthropic"
                            else DEFAULT_OPENAI_MODEL)

    batch_paths = [Path(b) for b in args.batches]
    for b in batch_paths:
        if not b.exists():
            raise SystemExit(f"Batch file not found: {b}")

    if args.dry_run:
        pairs = json.loads(batch_paths[0].read_text(encoding="utf-8"))
        print(build_prompt(rubric, pairs))
        return 0

    for b in batch_paths:
        process_batch(b, rubric, args.provider, model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
