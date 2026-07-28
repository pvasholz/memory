import csv
import json
import re
import sys
import requests
from datetime import datetime
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

# Retrieval policy (v2): over-fetch, then apply a RELATIVE score floor.
# Rationale: similarity-score magnitudes are corpus-size-dependent; in a small
# store, absolute thresholds (the old 0.25) wrongly wipe real hits or pass junk.
# Policy (after MiMoCode): always keep the #1 hit; keep others scoring at least
# RELATIVE_FLOOR of the top hit's score; cap at RETRIEVE_LIMIT.
RETRIEVE_LIMIT = 5
OVERFETCH_FACTOR = 3
RELATIVE_FLOOR = 0.15

def retrieve_memories(query):
    try:
        try:
            results = mem.search(query, filters={"user_id": USER_ID},
                                 limit=RETRIEVE_LIMIT * OVERFETCH_FACTOR)
        except TypeError:
            # some 2.x builds renamed the count param to top_k
            results = mem.search(query, filters={"user_id": USER_ID},
                                 top_k=RETRIEVE_LIMIT * OVERFETCH_FACTOR)
    except Exception as e:
        print(f"[Memory retrieval error: {e}]")
        return "", []

    if isinstance(results, dict):
        results = results.get("results", results.get("memories", []))

    if not results:
        return "", []

    scored = []
    for m in results:
        if not isinstance(m, dict):
            continue
        md = m.get("metadata") or {}
        if isinstance(md, dict) and md.get("type") == "directive":
            continue  # directives are injected verbatim via their own lane, never summarized here
        scored.append((m.get("score", 0) or 0, m.get("memory", str(m))))

    scored.sort(key=lambda x: -x[0])
    if not scored:
        return "", []

    top_score = scored[0][0]
    kept = [scored[0]]  # never drop the best hit, whatever its absolute score
    for s, t in scored[1:]:
        if top_score > 0 and s >= RELATIVE_FLOOR * top_score:
            kept.append((s, t))
    kept = kept[:RETRIEVE_LIMIT]

    memory_texts = [f"- {t} (relevance: {s:.3f})" for s, t in kept]
    raw_memories = [{"memory": t, "score": s} for s, t in kept]

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
    log_file = f"ariel_ollama_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

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