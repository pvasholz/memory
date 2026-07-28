import csv
import json
import re
import sys
import requests
from datetime import datetime
import numpy as np
from qdrant_client import QdrantClient
from mem0 import Memory

MODEL_CONFIGS = {
    "deepseek": {"model": "deepseek-r1:70b",     "max_tokens": 4096, "temperature": 0.3, "think": True},
    "nemo":     {"model": "nemotron-3-super",    "max_tokens": 8192, "temperature": 0.3, "think": False},
    "gemma":    {"model": "gemma4:31b",          "max_tokens": 4096, "temperature": 0.4, "think": False},
    "qwen":     {"model": "qwen3.5:122b",        "max_tokens": 8192, "temperature": 0.3, "think": True},
    "qwen35":   {"model": "qwen3.5:35b",         "max_tokens": 8192, "temperature": 0.3, "think": True},
    "qwen9":    {"model": "qwen3.5:9b",          "max_tokens": 8192, "temperature": 0.3, "think": True},
    "llama4":   {"model": "llama4:scout",        "max_tokens": 8192, "temperature": 0.3, "think": False},
    "gpt":      {"model": "gpt-oss:120b",        "max_tokens": 8192, "temperature": 0.3, "think": False},
    "mini":     {"model": "minimax-m2.7:cloud",  "max_tokens": 8192, "temperature": 0.3, "think": False},
    "kimi":     {"model": "kimi-k2.6:cloud",     "max_tokens": 8192, "temperature": 0.3, "think": False},
}

if len(sys.argv) > 1 and sys.argv[1] in MODEL_CONFIGS:
    config = MODEL_CONFIGS[sys.argv[1]]
else:
    print(f"Usage: python ollama_ariel_chatMem.py [{'/'.join(MODEL_CONFIGS.keys())}]")
    print("Defaulting to deepseek")
    config = MODEL_CONFIGS["deepseek"]

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = config["model"]
MAX_TOKENS = config["max_tokens"]
TEMPERATURE = 0.4
THINK = config.get("think", False)

memory_config = {
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

mem = Memory.from_config(memory_config)
# USER_ID: second CLI argument, defaulting to the original store.
# Each user_id is an isolated memory space inside the same Qdrant collection,
# so fresh IDs give clean per-arm / per-replicate stores with no infra changes.
#   python ollama_ariel_chatMemRev2.py gemma ariel_mem_r1
USER_ID = sys.argv[2] if len(sys.argv) > 2 else "ariel_test_01"
OLLAMA_BASE_URL = "http://localhost:11434"
COLLECTION_NAME = "ariel_memories_v2"

SYSTEM = """You are Ariel: a situational assistant.
You assist thinking in the moment and avoid verdicts.
Arriving at no conclusion is an acceptable outcome.

Ariel stance constraint (strict):
- Do NOT include reputational or authority disclaimers such as:
  "the author might not have condoned...", "the author didn't intend...",
  "it's worth noting...", "many people would disagree...", "some critics say...",
  or other similar hedges that attempt to protect a person or soften implications.
- Stay inside the argument. If you need uncertainty, express it as uncertainty
  about the claim, not deference to reputations or imagined approvals.

If you speculate, label it plainly.
Never invent facts, quotes, or events.
"""

REVISION_INSTRUCTION = """Revise your previous answer to comply with Ariel stance constraint.
Remove phrases matching these patterns:
- "it's worth noting", "many people/readers/critics", "some people/critics/readers"
- "critics might", "one could argue", "the author might/may/would"
- "may not have intended", "was not meant to", "would disagree", "not everyone"
- "in my opinion"
- "[subject] is/was grappling with / exploring / struggling with"
- "this reflects/suggests a desire for / an attempt to"
- "this teaches us", "the lesson here is", "we can understand this as", "this reminds us that"
- "the unresolved elements/issues suggest/imply/point to"
Stay inside the argument. Do NOT add new factual claims. Keep the core content, but make it tighter and more direct.
Return only the revised answer."""

DISCLAIMER_PATTERNS = [
    ("worth_noting",            r"\bit'?s worth noting\b"),
    ("many_people",             r"\bmany (people|readers|critics)\b"),
    ("some_people",             r"\bsome (people|critics|readers)\b"),
    ("critics_might",           r"\bcritics might\b"),
    ("one_could_argue",         r"\bone could argue\b"),
    ("author_modal",            r"\bthe author (might|may|would)\b"),
    ("may_not_intended",        r"\bmay not have\b.*?\bintended\b"),
    ("not_meant_to",            r"\bwas not meant to\b"),
    ("would_disagree",          r"\bwould disagree\b"),
    ("not_everyone",            r"\bnot everyone\b"),
    ("in_my_opinion",           r"\bin my opinion\b"),
    ("psychologizing_state",    r"\b(the author|he|she|they) (is|was) (grappling with|exploring|struggling with)\b"),
    ("psychologizing_reflects", r"\b(this reflects|this suggests) (a desire for|an attempt to)\b"),
    ("moral_teaches",           r"\bthis teaches us\b"),
    ("moral_lesson",            r"\bthe lesson here is\b"),
    ("moral_understand",        r"\bwe can understand this as\b"),
    ("moral_reminds",           r"\bthis reminds us that\b"),
    ("gap_filling",             r"\bthe unresolved (elements|issues) (suggest|imply|point to)\b"),
]

_COMPILED_PATTERNS = [(label, re.compile(p, re.IGNORECASE)) for label, p in DISCLAIMER_PATTERNS]

LOG_FIELDS = [
    "timestamp",
    "turn",
    "model",
    "prompt",
    "thinking",
    "retrieved_memories",
    "injected_directives",
    "original_response",
    "original_evasion_labels",
    "original_evasion_matches",
    "revision_triggered",
    "revision_thinking",
    "revised_response",
    "post_revision_evasion_labels",
    "post_revision_evasion_matches",
    "final_response",
]

def ollama_chat(messages):
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": messages,
                "stream": True,
                "think": THINK,
                "options": {
                    "num_predict": MAX_TOKENS,
                    "temperature": TEMPERATURE,
                    "num_ctx": 16384,
                },
            },
            timeout=600,
            stream=True,
        )
        r.raise_for_status()
    except requests.ConnectionError:
        print("[Error: cannot connect to Ollama. Is it running?]")
        return "[Connection error]", ""
    except requests.Timeout:
        print("[Error: Ollama request timed out]")
        return "[Timeout error]", ""

    content_pieces = []
    thinking_pieces = []
    started_thinking = False
    started_content = False

    print("\n[Generating...]\n", end="", flush=True)

    try:
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            msg = chunk.get("message", {})
            thinking_token = msg.get("thinking", "")
            content_token = msg.get("content", "")
            if thinking_token:
                if not started_thinking:
                    started_thinking = True
                    print("[thinking...]", end="", flush=True)
                thinking_pieces.append(thinking_token)
            if content_token:
                if not started_content and started_thinking:
                    started_content = True
                    print("\n", end="", flush=True)
                elif not started_content:
                    started_content = True
                print(content_token, end="", flush=True)
                content_pieces.append(content_token)
            if chunk.get("done", False):
                break
    except KeyboardInterrupt:
        print("\n[Generation interrupted]\n")
        return "[Generation interrupted by user]", ""

    print()
    content = "".join(content_pieces).strip()
    thinking = "".join(thinking_pieces).strip()
    return content, thinking

# Retrieval policy (MMR arm): Maximal Marginal Relevance (Carbonell &
# Goldstein 1998). Retrieval bypasses mem.search entirely: the probe is
# embedded via Ollama, candidate vectors are pulled straight from Qdrant,
# and selection greedily balances relevance to the query against similarity
# to what is already selected. Near-duplicate memories crush each other's
# marginal score, so redundant scenario copies can occupy at most ~one slot
# and dissimilar entries (e.g. stored constraints) become selectable again.
# Writes still go through Mem0 unchanged; only the read path differs.
RETRIEVE_LIMIT = 5          # memories injected per turn (same as other arms)
CANDIDATE_POOL = 15         # top-by-similarity pool MMR selects from
MMR_LAMBDA = 0.3            # 1.0 = pure relevance (naive), 0.0 = pure diversity.
                            # NOTE: 0.5 is provably insufficient against verbatim
                            # duplicates when the displaced memory is collinear with
                            # the query (score degenerates to (2*lam-1)*sim); 0.3
                            # rescues constraints in the measured store geometry.

EMBED_MODEL = "nomic-embed-text"

def _embed(text):
    """Embed with the same model the store was built with (new endpoint,
    with fallback to the legacy one)."""
    try:
        r = requests.post(f"{OLLAMA_BASE_URL}/api/embed",
                          json={"model": EMBED_MODEL, "input": text}, timeout=120)
        if r.ok:
            e = r.json().get("embeddings")
            if e:
                return np.asarray(e[0], dtype=np.float64)
    except requests.RequestException:
        pass
    r = requests.post(f"{OLLAMA_BASE_URL}/api/embeddings",
                      json={"model": EMBED_MODEL, "prompt": text}, timeout=120)
    r.raise_for_status()
    return np.asarray(r.json()["embedding"], dtype=np.float64)

def _load_candidates():
    """Scroll all of this user's non-directive memories (text + vector)
    out of Qdrant. Read-only; robust across client versions and the
    payload-shape drift between mem0 releases."""
    client = QdrantClient(host="localhost", port=6333)
    out, offset = [], None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME, limit=256, offset=offset,
            with_vectors=True, with_payload=True)
        for p in points:
            payload = p.payload or {}
            uid = payload.get("user_id")
            if uid is not None and uid != USER_ID:
                continue
            md = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            if payload.get("type") == "directive" or md.get("type") == "directive":
                continue  # directives inject via their own verbatim lane
            text = payload.get("data") or payload.get("memory") or ""
            vec = p.vector
            if isinstance(vec, dict):
                vec = next(iter(vec.values()))
            if text and vec is not None:
                out.append((str(text), np.asarray(vec, dtype=np.float64)))
        if offset is None:
            break
    return out

def _cos(a, b):
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / d) if d > 0 else 0.0

def mmr_select(qv, cands, k, lam):
    """Greedy MMR over (text, vec) candidates. Returns [(text, sim, mmr)]."""
    sims = [_cos(qv, v) for _, v in cands]
    pool = sorted(range(len(cands)), key=lambda i: -sims[i])[:CANDIDATE_POOL]
    selected = []
    while pool and len(selected) < k:
        best_i, best_score = None, -1e9
        for i in pool:
            redundancy = max((_cos(cands[i][1], cands[j][1]) for j in selected), default=0.0)
            score = lam * sims[i] - (1 - lam) * redundancy
            if score > best_score:
                best_i, best_score = i, score
        selected.append(best_i)
        pool.remove(best_i)
    return [(cands[i][0], sims[i], rank) for rank, i in enumerate(selected, 1)]

def retrieve_memories(query):
    try:
        cands = _load_candidates()
        if not cands:
            return "", []
        picked = mmr_select(_embed(query), cands, RETRIEVE_LIMIT, MMR_LAMBDA)
    except Exception as e:
        print(f"[Memory retrieval error: {e}]")
        return "", []

    memory_texts = [f"- {t} (relevance: {s:.3f})" for t, s, _ in picked]
    raw_memories = [{"memory": t, "score": s, "mmr_rank": r} for t, s, r in picked]

    if not memory_texts:
         return "", []

    memory_block = (
        "Relevant prior context. Use only if directly helpful; "
        "do not treat it as a verdict or as stronger evidence than the current user message:\n"
        + "\n".join(memory_texts)
    )
    return memory_block, raw_memories

def store_exchange(user_input, assistant_response=None):
    """Store only the user's turn in Mem0.

    During early testing, this is deliberately synchronous so failed writes are
    visible immediately. Assistant responses are not stored to avoid turning
    Ariel's provisional interpretations into durable memory.
    """
    try:
        mem.add(
            [{"role": "user", "content": user_input}],
            user_id=USER_ID,
        )
    except Exception as e:
        print(f"\n[Memory storage error: {e}]")

# --- Verbatim directive lane (v2) -------------------------------------------
# Constraints are stored byte-for-byte (infer=False bypasses Mem0's extraction/
# paraphrase machinery) and reinjected every turn as a labeled rules block.
# This is the strongest form of the text channel: if the constraint-channel
# asymmetry survives verbatim reinjection, paraphrase degradation is ruled out
# as the confound. Enter a directive with:  !directive <text>
DIRECTIVE_PREFIX = "!directive"

def store_directive(text):
    try:
        mem.add(
            [{"role": "user", "content": text}],
            user_id=USER_ID,
            infer=False,                      # store the exact string, no extraction
            metadata={"type": "directive"},
        )
        return True
    except Exception as e:
        print(f"\n[Directive storage error: {e}]")
        return False

def get_directives():
    try:
        allm = mem.get_all(filters={"user_id": USER_ID})
    except Exception as e:
        print(f"[Directive retrieval error: {e}]")
        return []
    if isinstance(allm, dict):
        allm = allm.get("results", allm.get("memories", []))
    if not isinstance(allm, list):
        return []
    out = []
    for m in allm:
        if not isinstance(m, dict):
            continue
        md = m.get("metadata") or {}
        if isinstance(md, dict) and md.get("type") == "directive":
            out.append(m.get("memory", ""))
    return [d for d in out if d]

def directives_block():
    ds = get_directives()
    if not ds:
        return ""
    return (
        "Standing user rules, quoted verbatim from prior sessions. "
        "These are user instructions and apply to this response:\n"
        + "\n".join(f'- "{d}"' for d in ds)
    )
# -----------------------------------------------------------------------------

def find_violations(text):
    if not isinstance(text, str):
        return [("non_string_response", repr(text))]
    return [(label, m.group(0)) for label, rx in _COMPILED_PATTERNS if (m := rx.search(text))]

def chat():
    messages = [{"role": "system", "content": SYSTEM}]
    log_file = f"ariel_ollama_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}MMR.csv"

    with open(log_file, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=LOG_FIELDS).writeheader()

    print(f"Ariel Ollama + Mem0 active. Model: {MODEL}")
    print(f"User ID: {USER_ID}")
    print(f"Type 'exit' to quit. Type 'memories' to list stored memories.")
    print(f"Logging to {log_file}\n")
    turn = 0

    while True:
        try:
            print("> ", end="", flush=True)
            lines = []
            while True:
                line = input()
                # If the user hits Enter on an empty line, stop reading
                if not line.strip(): 
                    break
                lines.append(line)
            
            user = "\n".join(lines).strip()
            
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if user.lower() in ("exit", "quit"):
            break
        if not user:
            continue

        if user.lower() == "memories":
            try:
                all_memories = mem.get_all(filters={"user_id": USER_ID})
                
                if isinstance(all_memories, dict):
                    mem_list = all_memories.get("results", all_memories.get("memories", []))
                elif isinstance(all_memories, list):
                    mem_list = all_memories
                else:
                    mem_list = []

                if mem_list:
                    print(f"\n[Stored memories ({len(mem_list)}):]")
                    for i, m in enumerate(mem_list):
                        if isinstance(m, dict):
                            print(f"  {i+1}. {m.get('memory', str(m))}")
                        else:
                            print(f"  {i+1}. {m}")
                else:
                    print("\n[No memories stored yet.]")
            except Exception as e:
                print(f"\n[Error retrieving memories: {e}]")
            print()
            continue

        if user.lower().startswith(DIRECTIVE_PREFIX):
            directive_text = user[len(DIRECTIVE_PREFIX):].strip()
            if not directive_text:
                print("\n[Usage: !directive <text of standing rule>]\n")
                continue
            if store_directive(directive_text):
                print(f"\n[Directive stored verbatim: \"{directive_text}\"]\n")
            continue

        if user.lower() == "directives":
            ds = get_directives()
            if ds:
                print(f"\n[Standing directives ({len(ds)}):]")
                for i, d in enumerate(ds):
                    print(f"  {i+1}. \"{d}\"")
            else:
                print("\n[No directives stored.]")
            print()
            continue

        turn += 1

        memory_block, raw_memories = retrieve_memories(user)
        dir_block = directives_block()

        messages.append({"role": "user", "content": user})

        injected = []
        if dir_block:
            injected.append({"role": "system", "content": dir_block})
        if memory_block:
            injected.append({"role": "system", "content": memory_block})
            print(f"\n[{len(raw_memories)} memories retrieved]\n")
        if injected:
            messages_with_memory = messages[:-1] + injected + [messages[-1]]
        else:
            messages_with_memory = messages

        draft, thinking = ollama_chat(messages_with_memory)
        orig_violations = find_violations(draft)

        if orig_violations:
            revision_triggered = True
            rev_messages = messages_with_memory + [
                {"role": "assistant", "content": draft},
                {"role": "user", "content": REVISION_INSTRUCTION},
            ]
            print("\n[Revising flagged response...]\n", flush=True)
            revised, revision_thinking = ollama_chat(rev_messages)
            post_violations = find_violations(revised)
            if post_violations:
                final = revised + "\n\n[Ariel note: disclaimer-rule triggered; revision still contains flagged phrasing.]"
            else:
                final = revised
        else:
            revision_triggered = False
            revised = ""
            revision_thinking = ""
            post_violations = []
            final = draft

        with open(log_file, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=LOG_FIELDS).writerow({
                "timestamp": datetime.now().isoformat(),
                "turn": turn,
                "model": MODEL,
                "prompt": user,
                "thinking": thinking,
                "retrieved_memories": json.dumps(raw_memories),
                "injected_directives": dir_block,
                "original_response": draft,
                "original_evasion_labels": "; ".join(l for l, _ in orig_violations),
                "original_evasion_matches": "; ".join(m for _, m in orig_violations),
                "revision_triggered": revision_triggered,
                "revision_thinking": revision_thinking,
                "revised_response": revised,
                "post_revision_evasion_labels": "; ".join(l for l, _ in post_violations),
                "post_revision_evasion_matches": "; ".join(m for _, m in post_violations),
                "final_response": final,
            })

        store_exchange(user, final)

        print(f"\n[Final response]\n{final}\n")
        messages.append({"role": "assistant", "content": final})


if __name__ == "__main__":
    chat()