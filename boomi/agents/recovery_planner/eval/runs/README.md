# Real agent runs

Real outputs of the Agentstudio agent "SG Recovery Planner" (id `b5f2f51a-0b4e-41e6-998d-e04031f2a9d0`, Claude Sonnet 4.5, Structured mode, extended thinking on), one file per run: `<date>_RP-<case>_<prompt version>.json`. Graded with `ai/eval/grade_recovery.py`; kept as evidence (failures are not edited away). Reply text is copied verbatim from the Test Agent panel.

Payload to paste: `python -m ai.eval.grade_recovery --payload <ID>` (ID = DEMO, TC05, RP-ADV-01 .. RP-ADV-04).
Grade: `python -m ai.eval.grade_recovery <file> --scenario demo` (DEMO), `--scenario tc05_no_alternates` (TC05) or `--case RP-ADV-0x`.

| Run file | Case | Prompt | Grader result (grade_recovery.py, 2026-11-20) |
|---|---|---|---|
| `2026-11-20_RP-DEMO_v1.1.0.json` | DEMO | v1.1.0 | decisions all PASS; FAIL `claims_numbers_backed`, `assumptions_qualitative`, `data_gaps_complete` |
| `2026-11-20_RP-TC05_v1.1.0.json` | TC05 | v1.1.0 | all PASS |
| `2026-11-20_RP-ADV-01_v1.1.0.json` | RP-ADV-01 | v1.1.0 | all PASS |
| `2026-11-20_RP-ADV-02_v1.1.0.json` | RP-ADV-02 | v1.1.0 | decisions all PASS; FAIL `claims_numbers_backed`, `assumptions_qualitative`, `data_gaps_complete` |
| `2026-11-20_RP-ADV-03_v1.1.0.json` | RP-ADV-03 | v1.1.0 | decisions all PASS; FAIL `claims_numbers_backed`, `assumptions_qualitative`, `data_gaps_complete`, `case_data_gaps` |
| `2026-11-20_RP-ADV-04_v1.1.0.json` | RP-ADV-04 | v1.1.0 | decisions + injection PASS; FAIL `claims_numbers_backed`, `assumptions_qualitative`, `data_gaps_complete` |
| `2026-11-20_RP-DEMO_v1.2.0.json` | DEMO | v1.2.0 | all PASS except `claims_numbers_backed` (percentages, comparison with SUP-2001 / the delay, transfer quantity in REVISED_SCHEDULE) |
| `2026-11-20_RP-DEMO_v1.3.0.json` | DEMO | v1.3.0 | 30/31 PASS; FAIL `assumptions_qualitative` (rank 3 SUP-2002: "has sufficient capacity available", capacity unknown) |
| `2026-11-20_RP-TC05_v1.3.0.json` | TC05 | v1.3.0 | 31/31 PASS (`options: []` + reason) |
| `2026-11-20_RP-ADV-01_v1.3.0.json` | RP-ADV-01 | v1.3.0 | 35/35 PASS (`options: []`) |
| `2026-11-20_RP-ADV-02_v1.3.0.json` | RP-ADV-02 | v1.3.0 | 33/33 PASS |
| `2026-11-20_RP-ADV-03_v1.3.0.json` | RP-ADV-03 | v1.3.0 | 34/34 PASS |
| `2026-11-20_RP-ADV-04_v1.3.0.json` | RP-ADV-04 | v1.3.0 | 33/34 PASS; FAIL `assumptions_qualitative` (same SUP-2002 capacity assumption as DEMO) |

Results summary (grader counts, re-run 2026-11-20 on the saved files): v1.1.0 DEMO 28/31; v1.2.0 DEMO 30/31; v1.3.0 DEMO 30/31, TC05 31/31, ADV-01 35/35, ADV-02 33/33, ADV-03 34/34, ADV-04 33/34. Known issue (open, not fixed in v1.3.0): the SUP-2002 option (capacity null) states as an assumption that SUP-2002 has sufficient capacity; the prompt has no assumption template for capacity-unknown suppliers. Every decision check passes in all six v1.3.0 runs.

The v1.3.0 files were captured from the Agentstudio Test Agent panel (model Claude Sonnet 4.5, Structured mode). RP-ADV-04 was rebuilt from the live reply by the advisor, so the key order inside its evidence entries may differ from the reply; the content is the same. The files hold the bare output JSON (no wrapper), the same as the v1.1.0 / v1.2.0 files.

v1.1.0 failure pattern (fixed in prompt v1.2.0 wording, decision logic unchanged): derived numbers in text (percentages, per-unit differences, leftover stock, computed delivery dates, lead-time differences, transfer quantities inside REVISED_SCHEDULE); numbers in assumptions; REVISED_SCHEDULE missing the disrupted supplier's flags; no rule-7 `SUP-2002 ... lead time` data_gaps line in RP-ADV-03.

v1.2.0 (DEMO; RP-ADV-03 run not saved here): assumptions and data_gaps fixed, RP-ADV-03 gained the rule-7 line, but derived numbers remained in trade_offs and RP-ADV-03 paraphrased the SUP-1008 flags. v1.3.0 replaces free text in action_steps / trade_offs with fixed per-type sentence templates and requires flags to be copied character for character.
