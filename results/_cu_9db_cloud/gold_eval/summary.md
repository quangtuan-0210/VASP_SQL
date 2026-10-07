# Gold-SQL regression check (Step 1)

> Run 2026-10-02 on 9/11 BIRD dev databases (codebase_community and european_football_2 excluded: files >400 MB could not be copied to the run environment). Re-run on your machine for the full set.

Evaluated: **1218** samples (gold exec errors skipped: 1)

| Status | Count | % |
|---|---:|---:|
| UNCHANGED | 1191 | 97.78 |
| REPAIRED | 3 | 0.25 |
| ABSTAIN | 24 | 1.97 |
| CRASH | 0 | 0.0 |

Regression (REPAIRED and result no longer matches gold): **0** (0.0%)

## False alarms by first failing gate

| Gate | Count |
|---|---:|
| B | 27 |

## False alarms by cause

| Cause | Count |
|---|---:|
| unjoined | 19 |
| could not be resolved | 8 |

## Per database

| DB | UNCHANGED | REPAIRED | ABSTAIN | CRASH |
|---|---:|---:|---:|---:|
| california_schools | 87 | 0 | 2 | 0 |
| card_games | 174 | 3 | 13 | 0 |
| debit_card_specializing | 63 | 0 | 1 | 0 |
| financial | 106 | 0 | 0 | 0 |
| formula_1 | 170 | 0 | 4 | 0 |
| student_club | 158 | 0 | 0 | 0 |
| superhero | 129 | 0 | 0 | 0 |
| thrombosis_prediction | 161 | 0 | 2 | 0 |
| toxicology | 143 | 0 | 2 | 0 |
