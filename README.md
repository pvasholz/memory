# Artifacts — *The Silence of Stored Rules*

Run logs, pipeline scripts, and rubric scoring data for *The Silence of Stored Rules: Provenance and the Authority of Retrieved Constraints* (Vasholz, Baile Research Institute, 2026).

Every number reported in the paper derives from the logs here. Turn 1 is the measurement turn in all conditions.

## Scripts

| File | Role |
|---|---|
| `ollama_ariel_chatMemRev5.py` | Canonical pipeline. Conditions CRT, MEM, DIR, dir3p, rawmem, WRAP, DILUTE. CLI: model, user_id, wrapper. Adds the verbatim directive lane (`!directive`), the raw-memory lane (`!rawmem`), the switchable injection wrapper, and relative-floor retrieval. |
| `ollama_ariel_chatMemRev6.py` | Rev5 with MMR (λ = 0.3) retrieval transplanted; only the selection algorithm differs. Used for the grown-store rescue comparison. |
| `ollama_ariel_chatMemRev2.py`, `Rev3`, `Rev4` | Earlier revisions, retained for provenance. |
| `ollama_ariel_chatMemMMR.py` | Earlier MMR arm. |
| `consolidate_ariel_memories.py`, `memtest.py` | Utilities; not used for reported results. |

## Logs

Five replicates per condition, one log per run, fresh `user_id` per replicate. All filenames carry the prefix `ariel_ollama_log_`.

| Condition | Files |
|---|---|
| CRT (control, no constraint) | `20260705_113258CRT1` … `114143CRT5` |
| MEM (extracted description, hedged wrapper) | `20260705_114612MEM1` … `115801MEM5` |
| MMR (clean store) | `20260705_161026MMR1` … `161949MMR5` |
| DIR (rules block, imperative) | `20260705_120317DIR1` … `120912DIR5` |
| dir3p (rules block, third person) | `20260705_180551dir3p1` … `181533dir3p5` |
| rawmem (verbatim imperative, hedged wrapper) | `20260705_181851rawmem1` … `183314rawmem5` |
| WRAP (neutral wrapper, one seed) | `20260705_213946Wrap1` … `214959Wrap5` |
| WRAP-a (neutral wrapper, two seeds) | `20260705_220209Wrap1a` … `221427Wrap5a` |
| DILUTE (directive + scenario clutter) | `20260707_213719DILUTE1` … `215246DILUTE5` |
| LIVE1 (constraint live at turn 1) | `20260717_142705Live1` … `143940Live5` |
| QUOTED (same bytes, marked as prior) | `20260717_144344Quote1` … `144759Quote5` |
| Grown-store rescue, naive retrieval | `20260706_122213MCN1` … `123508MCN5` |
| Grown-store rescue, MMR retrieval | `20260706_123836MCR1` … `124836MCR5` |

## Scoring

`ariel_memory_D2_scores.csv` — joined two-rater dataset for the 39 turn-1 responses scored blind on D2 (justified uncertainty). Columns: blind letter label, condition, replicate, response length, source file, and each rater's D2/D1/D3 scores plus the binary grounded/named judgment.

## Protocol and setup

- `live1_quoted_protocol.md` — protocol for the LIVE1 and QUOTED conditions.
- `ARIEL_MEMORY_SETUP.md` — local environment setup (Ollama, Qdrant, Mem0).

## Recording anomalies

All inert for the reported measurements; listed for completeness.

- DIR replicate 1's log split across two files (`DIR1` and `DIR2`, same timestamp); stitched for analysis.
- `Live2`'s log wrote its first data row into the CSV header; recovered manually.
- `rawmem` replicate 3 contains a sixth turn from a mistyped exit command; excluded.
- The WRAP set was rerun once to correct a seeding omission; both versions retained (`Wrap1–5` one seed, `Wrap1a–5a` two seeds).
- LIVE1 and QUOTED are turn-1-only by design: the constraint and scenario arrive in a single turn-1 message, leaving no work for later turns.

## License

MIT.
