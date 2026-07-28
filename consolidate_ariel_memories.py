"""
consolidate_ariel_memories.py — "dream"-style consolidation pass for the Ariel memory store.

WHAT THIS DOES (plain language)
--------------------------------
Reads every memory in the Qdrant collection `ariel_memories_v2`, shows them to a
local Ollama model alongside a digest of your raw CSV run logs (the logs are the
authoritative trajectory; the store is just an index), and asks the model to
propose consolidation actions: KEEP, REWRITE, MERGE, or DELETE — each with a
reason and a provenance pointer back to the logs.

SAFETY MODEL (the part to trust)
--------------------------------
1. DRY RUN BY DEFAULT. Running with no flags changes NOTHING. It only writes a
   proposals CSV (the audit log) that you read before deciding anything.
2. --apply is required to mutate the store, and the FIRST thing --apply does is
   export the entire collection to a timestamped JSON snapshot. Worst case is a
   wasted pass, never a lost store.
3. Mechanical guardrails the model cannot talk its way past:
   - every DELETE/REWRITE/MERGE must cite a memory id that actually exists;
   - at most MAX_ACTIONS mutating actions per pass (excess logged, not applied);
   - the pass refuses to apply if it would remove more than REFUSE_IF_REMOVES_OVER
     of the store in one go;
   - the prompt instructs "when in doubt, KEEP."
4. Every proposal — applied or not — lands in the audit CSV. The audit CSV is
   also the research instrument: it is a log of forgetting events with reasons.

USAGE
-----
  python consolidate_ariel_memories.py                  # dry run (safe, default)
  python consolidate_ariel_memories.py --apply          # snapshot, then apply
  python consolidate_ariel_memories.py --model qwen3.5:35b
  python consolidate_ariel_memories.py --logs "ariel_ollama_log_*.csv"
  python consolidate_ariel_memories.py --max-actions 10

Requires: Ollama running locally, Qdrant running locally, mem0 installed —
i.e., the same environment as ollama_ariel_chatMemRev.py.
"""

import argparse
import csv
import glob
import json
import re
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration — mirrors ollama_ariel_chatMemRev.py exactly. If you change
# the collection or models there, change them here too.
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
USER_ID = "ariel_test_01"

MEMORY_CONFIG = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "llama3.1:8b",
            "temperature": 0.0,
            "ollama_base_url": "http://localhost:11434",
        },
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": "nomic-embed-text",
            "ollama_base_url": "http://localhost:11434",
        },
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": "localhost",
            "port": 6333,
            "collection_name": "ariel_memories_v2",
            "embedding_model_dims": 768,
        },
    },
}

# Guardrail defaults (overridable from the command line where noted).
MAX_ACTIONS_DEFAULT = 15          # max mutating actions (delete/rewrite/merge) per pass
REFUSE_IF_REMOVES_OVER = 0.40     # refuse to --apply if >40% of entries would vanish
LOG_PROMPT_CHARS = 240            # truncation for each logged prompt in the trajectory digest
MAX_LOG_ROWS = 200                # most-recent rows of trajectory shown to the consolidator

AUDIT_FIELDS = [
    "timestamp", "action", "entry_ids", "old_text", "new_text",
    "reason", "provenance", "status",
]

# ---------------------------------------------------------------------------
# The consolidation prompt — gating rules adapted from MiMoCode's dream pass:
# trajectory is authoritative, evidence-gated promotion, merge-don't-append,
# delete superseded / single-session detail, density over completeness,
# and (our addition, because the consolidator is a small model) a strict
# JSON output contract and an explicit when-in-doubt-KEEP rule.
# ---------------------------------------------------------------------------

CONSOLIDATION_PROMPT = """You are performing a memory consolidation pass.

You will be given:
1. MEMORY ENTRIES — the current contents of a long-term memory store. Each has a numeric index and an id.
2. TRAJECTORY DIGEST — excerpts from the raw session logs. The logs are authoritative; the memory store is only an index over them.

Propose consolidation actions. The allowed actions are:
- KEEP: the entry stays as-is. (Default. When in doubt, KEEP.)
- REWRITE: same fact, stated more densely or with a stale detail corrected. Provide new_text (1-3 lines).
- MERGE: two or more entries record the same fact; combine into one. Provide new_text (1-3 lines) and all source indices.
- DELETE: the entry is superseded by a newer entry or newer trajectory, contradicted by the trajectory, or was only ever relevant to a single session and is not a durable fact or standing rule.

Rules:
- Never DELETE a standing user rule or preference unless the trajectory shows it was explicitly revoked or replaced.
- Every REWRITE/MERGE/DELETE must include a short reason and a provenance pointer (timestamp or turn from the digest, or "store-internal: duplicates entry N").
- Do not invent facts. new_text may only restate what entries or digest already contain.
- Prefer few, dense entries over many thin ones. 1-3 lines per entry.
- When in doubt, KEEP.

Output format — return ONLY a JSON array, no prose, no code fences:
[
  {"action": "KEEP", "indices": [1]},
  {"action": "DELETE", "indices": [4], "reason": "...", "provenance": "..."},
  {"action": "MERGE", "indices": [2, 7], "new_text": "...", "reason": "...", "provenance": "..."},
  {"action": "REWRITE", "indices": [5], "new_text": "...", "reason": "...", "provenance": "..."}
]
Every entry index must appear in exactly one action.
"""

# ---------------------------------------------------------------------------
# Helpers (testable without mem0/qdrant present)
# ---------------------------------------------------------------------------

def normalize_get_all(raw):
    """mem0's get_all returns a dict in some versions and a list in others
    (same defensive pattern as ollama_ariel_chatMemRev.py). Normalize to a
    list of {id, memory} dicts, dropping anything malformed."""
    if isinstance(raw, dict):
        raw = raw.get("results", raw.get("memories", []))
    if not isinstance(raw, list):
        return []
    out = []
    for m in raw:
        if isinstance(m, dict) and m.get("id") is not None and m.get("memory"):
            out.append({"id": str(m["id"]), "memory": str(m["memory"])})
    return out


def load_trajectory_digest(logs_glob):
    """Read user prompts (timestamp, turn, prompt) from the run-log CSVs.
    Newest rows win when we exceed MAX_LOG_ROWS. Missing/odd files are
    skipped with a warning rather than crashing the pass."""
    rows = []
    for path in sorted(glob.glob(logs_glob)):
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    prompt = (r.get("prompt") or "").strip()
                    if not prompt:
                        continue
                    rows.append({
                        "timestamp": r.get("timestamp", ""),
                        "turn": r.get("turn", ""),
                        "prompt": prompt[:LOG_PROMPT_CHARS],
                        "file": path,
                    })
        except Exception as e:
            print(f"[warn] could not read log {path}: {e}")
    rows.sort(key=lambda r: r["timestamp"])
    return rows[-MAX_LOG_ROWS:]


def build_user_message(entries, digest):
    lines = ["MEMORY ENTRIES:"]
    for i, e in enumerate(entries, start=1):
        lines.append(f"{i}. (id={e['id']}) {e['memory']}")
    lines.append("")
    lines.append("TRAJECTORY DIGEST (newest last):")
    if digest:
        for r in digest:
            lines.append(f"[{r['timestamp']} turn {r['turn']}] {r['prompt']}")
    else:
        lines.append("(no logs found — be conservative: KEEP unless store-internal evidence of duplication)")
    return "\n".join(lines)


def extract_json_array(text):
    """Small models wrap JSON in fences or prose. Strip fences, then take the
    span from the first '[' to the last ']'. Raise if unparseable."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON array found in model output")
    return json.loads(cleaned[start:end + 1])


def validate_actions(actions, entries, max_actions):
    """Enforce the mechanical guardrails. Returns (valid, rejected) lists,
    where each rejected item carries a status explaining why. Indices are
    1-based as presented to the model."""
    n = len(entries)
    valid, rejected = [], []
    mutating_seen = 0
    claimed = set()
    for a in actions if isinstance(actions, list) else []:
        if not isinstance(a, dict):
            rejected.append(({"raw": repr(a)[:200]}, "skipped_malformed"))
            continue
        action = str(a.get("action", "")).upper()
        idxs = a.get("indices", [])
        if action not in ("KEEP", "DELETE", "REWRITE", "MERGE") or not isinstance(idxs, list) or not idxs:
            rejected.append((a, "skipped_malformed"))
            continue
        if not all(isinstance(i, int) and 1 <= i <= n for i in idxs):
            rejected.append((a, "skipped_bad_index"))        # cites an id that doesn't exist
            continue
        if any(i in claimed for i in idxs):
            rejected.append((a, "skipped_index_conflict"))   # entry already claimed by another action
            continue
        if action in ("REWRITE", "MERGE") and not str(a.get("new_text", "")).strip():
            rejected.append((a, "skipped_missing_text"))
            continue
        if action == "MERGE" and len(idxs) < 2:
            rejected.append((a, "skipped_malformed"))
            continue
        if action != "KEEP":
            mutating_seen += 1
            if mutating_seen > max_actions:
                rejected.append((a, "skipped_action_cap"))   # over the per-pass cap
                continue
        claimed.update(idxs)
        valid.append(a)
    return valid, rejected


def removal_fraction(valid_actions, n_entries):
    """Fraction of the store that would disappear (DELETEs plus the collapsed
    sources of MERGEs). Used by the refuse-to-apply valve."""
    if n_entries == 0:
        return 0.0
    removed = 0
    for a in valid_actions:
        if a["action"] == "DELETE":
            removed += len(a["indices"])
        elif a["action"] == "MERGE":
            removed += len(a["indices"]) - 1  # k sources become 1 entry
    return removed / n_entries


def call_ollama(model, system, user):
    """Non-streaming chat call, temperature 0, thinking off — the consolidator
    is a clerk, not an essayist."""
    r = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_ctx": 16384, "num_predict": 4096},
        },
        timeout=600,
    )
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Dream-style consolidation pass over the Ariel memory store.")
    ap.add_argument("--apply", action="store_true",
                    help="actually apply proposals (default is dry run). Snapshots the store first.")
    ap.add_argument("--model", default="llama3.1:8b",
                    help="Ollama model to use as the consolidator (default: llama3.1:8b)")
    ap.add_argument("--logs", default="ariel_ollama_log_*.csv",
                    help="glob for run-log CSVs used as the authoritative trajectory")
    ap.add_argument("--max-actions", type=int, default=MAX_ACTIONS_DEFAULT,
                    help=f"cap on mutating actions per pass (default {MAX_ACTIONS_DEFAULT})")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_path = f"consolidation_proposals_{ts}.csv"

    # Connect to the store (import here so the helpers above are testable
    # on machines without mem0 installed).
    from mem0 import Memory
    mem = Memory.from_config(MEMORY_CONFIG)

    entries = normalize_get_all(mem.get_all(user_id=USER_ID))
    if not entries:
        print("Store is empty — nothing to consolidate.")
        return
    print(f"Loaded {len(entries)} memory entries from '{MEMORY_CONFIG['vector_store']['config']['collection_name']}'.")

    digest = load_trajectory_digest(args.logs)
    print(f"Loaded {len(digest)} trajectory rows from logs matching '{args.logs}'.")

    print(f"Consolidator: {args.model} — requesting proposals...")
    raw = call_ollama(args.model, CONSOLIDATION_PROMPT, build_user_message(entries, digest))
    try:
        proposed = extract_json_array(raw)
    except Exception as e:
        # A failed parse is a wasted pass, not a corrupted store. Save the raw
        # output so the failure itself is inspectable data.
        fail_path = f"consolidation_rawfail_{ts}.txt"
        with open(fail_path, "w", encoding="utf-8") as f:
            f.write(raw)
        print(f"[error] could not parse model output as JSON ({e}). Raw output saved to {fail_path}. No changes made.")
        sys.exit(1)

    valid, rejected = validate_actions(proposed, entries, args.max_actions)
    mutating = [a for a in valid if a["action"] != "KEEP"]
    frac = removal_fraction(valid, len(entries))

    # ---- write the audit CSV (always, dry run or not) ----
    def entry_texts(idxs):
        return " || ".join(entries[i - 1]["memory"] for i in idxs)

    def entry_ids(idxs):
        return ";".join(entries[i - 1]["id"] for i in idxs)

    now_iso = datetime.now(timezone.utc).isoformat()
    with open(audit_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
        w.writeheader()
        for a in valid:
            w.writerow({
                "timestamp": now_iso,
                "action": a["action"],
                "entry_ids": entry_ids(a["indices"]),
                "old_text": entry_texts(a["indices"]),
                "new_text": a.get("new_text", ""),
                "reason": a.get("reason", ""),
                "provenance": a.get("provenance", ""),
                "status": "proposed",
            })
        for a, status in rejected:
            w.writerow({
                "timestamp": now_iso,
                "action": str(a.get("action", "?")),
                "entry_ids": str(a.get("indices", "")),
                "old_text": "", "new_text": str(a.get("new_text", ""))[:300],
                "reason": str(a.get("reason", ""))[:300],
                "provenance": str(a.get("provenance", ""))[:300],
                "status": status,
            })

    n_del = sum(1 for a in mutating if a["action"] == "DELETE")
    n_rw = sum(1 for a in mutating if a["action"] == "REWRITE")
    n_mg = sum(1 for a in mutating if a["action"] == "MERGE")
    print(f"\nProposals: {len(valid)} valid ({n_del} delete, {n_rw} rewrite, {n_mg} merge, "
          f"{len(valid) - len(mutating)} keep) | {len(rejected)} rejected by guardrails")
    print(f"Audit log written to {audit_path}")
    print(f"Removal fraction if applied: {frac:.0%} of {len(entries)} entries")

    if not args.apply:
        print("\nDRY RUN — no changes made. Read the audit CSV; rerun with --apply to enact.")
        return

    # ---- refuse-to-apply valve ----
    if frac > REFUSE_IF_REMOVES_OVER:
        print(f"\n[refused] This pass would remove {frac:.0%} of the store "
              f"(limit {REFUSE_IF_REMOVES_OVER:.0%}). Nothing applied. "
              f"If intentional, raise REFUSE_IF_REMOVES_OVER in the script and rerun.")
        sys.exit(2)

    # ---- snapshot before any mutation ----
    snap_path = f"ariel_memories_snapshot_{ts}.json"
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"Snapshot of {len(entries)} entries written to {snap_path}")

    # ---- apply, recording per-action outcomes back into the audit CSV ----
    results = []
    for a in mutating:
        ids = [entries[i - 1]["id"] for i in a["indices"]]
        try:
            if a["action"] == "DELETE":
                for mid in ids:
                    mem.delete(memory_id=mid)
            else:  # REWRITE and MERGE: remove sources, store new_text verbatim.
                for mid in ids:
                    mem.delete(memory_id=mid)
                # infer=False stores the text as-is instead of re-running
                # mem0's extraction (which is the paraphrase machinery we are
                # deliberately bypassing here).
                mem.add([{"role": "user", "content": a["new_text"].strip()}],
                        user_id=USER_ID, infer=False)
            results.append((a, "applied"))
        except Exception as e:
            results.append((a, f"apply_failed: {e}"))

    with open(audit_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
        for a, status in results:
            w.writerow({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": a["action"],
                "entry_ids": entry_ids(a["indices"]),
                "old_text": "", "new_text": a.get("new_text", ""),
                "reason": a.get("reason", ""), "provenance": a.get("provenance", ""),
                "status": status,
            })

    after = normalize_get_all(mem.get_all(user_id=USER_ID))
    ok = sum(1 for _, s in results if s == "applied")
    failed = len(results) - ok
    print(f"\nApplied {ok}/{len(results)} mutating actions ({failed} failed — see audit CSV).")
    print(f"Store size: {len(entries)} -> {len(after)} entries.")
    print(f"Recovery: snapshot at {snap_path}; audit trail at {audit_path}.")


if __name__ == "__main__":
    import requests  # placed here so helper functions are importable without it
    main()
