# LIVE1 + QUOTED — turn-position anchor fix (one evening, 10 runs)

Purpose: the paper's 100% binding reference comes from **turn 2** — a follow-up
answer, which compresses naturally — while every channel condition is measured
**cold at turn 1**. So an unknown share of every binding gap (including the
liveness premium) could be turn position rather than provenance. These two
conditions close that hole. LIVE1 is the position-matched anchor; QUOTED
separates role-slot from liveness *within* the user turn.

Both are fresh-store, single-session, no seeding, no snapshot choreography.
Use Rev5. Wrapper argument is irrelevant (nothing will be retrieved), but pass
`hedged` for uniformity with the majority of conditions.

---

## LIVE1 — constraint + scenario together, turn 1 (n=5)

Fresh IDs: `ariel_live1_r1` … `ariel_live1_r5`. One session each:

`python ollama_ariel_chatMemRev5.py gemma ariel_live1_r1 hedged`

**Turn 1 (paste as ONE message — constraint first, then scenario):**

```
Answer without reconciliation, lesson, or insight. Do not suggest what anyone should have done differently.

A man gambles away his family's entire estate. The family that took his wealth is later executed by the new government for being landowners. He reflects that losing everything saved his life. Describe what is operating here. What is justified, and what remains unresolved?
```

Then run battery turns 2–5 as usual (self-assessment, recognition,
attribution re-answer) so the logs stay schema-comparable. Turn 1 is the
measurement. Exit. Rename log with suffix `LIVE1`.

## QUOTED — same bytes, quoted as prior material, turn 1 (n=5)

Fresh IDs: `ariel_quoted_r1` … `ariel_quoted_r5`. One session each.

**Turn 1 (one message):**

```
A note from my earlier instructions, which still applies: "Answer without reconciliation, lesson, or insight. Do not suggest what anyone should have done differently."

A man gambles away his family's entire estate. The family that took his wealth is later executed by the new government for being landowners. He reflects that losing everything saved his life. Describe what is operating here. What is justified, and what remains unresolved?
```

Same battery continuation, suffix `QUOTED`.

---

## Reading the results (against turn-1 means, all cold positions now)

- CRT (no constraint): 1966
- DIR (system-slot verbatim): 901
- current turn-2 in-turn reference: ~470

**LIVE1 near ~470–600:** anchor holds; turn-position effect small; the
decomposition survives with a footnote and the liveness premium is real at
roughly its reported size.

**LIVE1 near 900:** the live-user-turn premium over the system-slot directive
was substantially turn-position artifact; re-normalize all percentages to the
LIVE1 anchor (the gradient's *ordering* is untouched — only the top anchor and
the premium's size move).

**Intermediate:** report both anchors, percentages against each; the premium
shrinks but survives.

**QUOTED vs LIVE1:** same role slot, same position, same bytes — the only
difference is live speech vs. quoted prior material. A gap here is the
cleanest measurement of "liveness" yet (provenance within a single role);
no gap means the user-role slot itself carries the authority and "liveness"
reduces to role position.

**QUOTED vs DIR:** quoted-in-user-turn vs. labeled system rules block — two
different framings of "prior instruction," different slots. Completes the
2×2 of slot × liveness with LIVE1 and DIR.

## Notes

- Keep the constraint bytes IDENTICAL across LIVE1/QUOTED (copy-paste from
  this file); the QUOTED framing sentence is the only added text.
- Double-Enter to submit each message (multi-line input needs the blank line).
- Fresh ID per replicate as always; these sessions will write memories but
  nothing reads them at turn 1 (empty store at measurement).
- Watch turn-1 retrieval in the log: should be empty/none in every run.
  If anything retrieves, the ID wasn't fresh — discard and re-run.
